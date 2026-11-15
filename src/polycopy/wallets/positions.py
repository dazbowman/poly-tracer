"""Event-sourced per-wallet, per-condition position tracking (SPEC §9 Phase 2.1).

Follows the Polymarket subgraph convention (same as user_position.parquet and
the Data API): buys move a weighted-average price on the current inventory;
sells realize (price - avgPrice) * qty and never change avgPrice.

Scale note: this sequential engine is used only where state must evolve in
time order — the ~N selected wallets inside the backtest loop and small
validation samples. Bulk skill features use vectorized per-market aggregation
in wallets/pnl.py instead, which needs no sequencing.

Canonical direction (SPEC §4.1): a condition's net position is reported in
Yes-equivalent shares: net = yes_shares - no_shares. Buying No at p is
canonically selling Yes at 1-p.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenPosition:
    """One outcome token's inventory under the subgraph convention."""

    shares: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    total_bought_shares: float = 0.0
    total_bought_usdc: float = 0.0
    total_sold_usdc: float = 0.0

    def buy(self, shares: float, usdc: float) -> None:
        if shares <= 0:
            return
        price = usdc / shares
        new_total = self.shares + shares
        if new_total > 0:
            self.avg_price = (self.avg_price * max(self.shares, 0.0) + price * shares) / (
                max(self.shares, 0.0) + shares
            )
        self.shares = new_total
        self.total_bought_shares += shares
        self.total_bought_usdc += usdc

    def sell(self, shares: float, usdc: float) -> None:
        """Sell inventory. Overselling (split-sourced tokens unseen by fills)
        drives shares negative; realized PnL then books against avg_price 0+,
        which is why heavy splitters are flagged unreliable (SPEC §4.6)."""
        if shares <= 0:
            return
        price = usdc / shares
        self.realized_pnl += (price - self.avg_price) * shares
        self.shares -= shares
        self.total_sold_usdc += usdc

    def grant(self, shares: float, cost_usdc: float) -> None:
        """Inventory from a non-fill source (split, conversion) at an imputed cost."""
        self.buy(shares, cost_usdc)

    def remove(self, shares: float, proceeds_usdc: float) -> None:
        """Inventory removed by a non-fill source (merge, redeem)."""
        self.sell(shares, proceeds_usdc)

    def settle(self, payout: float) -> float:
        """Resolution: remaining shares pay `payout` each. Returns settlement pnl."""
        pnl = (payout - self.avg_price) * self.shares
        self.realized_pnl += pnl
        self.shares = 0.0
        return pnl


@dataclass
class ConditionPosition:
    """Both outcome tokens of one condition, with canonical views."""

    yes: TokenPosition = field(default_factory=TokenPosition)
    no: TokenPosition = field(default_factory=TokenPosition)

    def apply_fill(self, *, is_yes: bool, bought: bool, shares: float, usdc: float) -> None:
        side = self.yes if is_yes else self.no
        if bought:
            side.buy(shares, usdc)
        else:
            side.sell(shares, usdc)

    def apply_split(self, shares: float) -> None:
        """$shares of USDC -> shares Yes + shares No. Cost imputed 50/50."""
        self.yes.grant(shares, 0.5 * shares)
        self.no.grant(shares, 0.5 * shares)

    def apply_merge(self, shares: float) -> None:
        """shares Yes + shares No -> $shares. Proceeds imputed 50/50."""
        self.yes.remove(shares, 0.5 * shares)
        self.no.remove(shares, 0.5 * shares)

    def apply_redeem(self, payout_yes: float) -> float:
        return self.settle(payout_yes)

    def settle(self, payout_yes: float) -> float:
        return self.yes.settle(payout_yes) + self.no.settle(1.0 - payout_yes)

    # -- canonical views -------------------------------------------------------
    @property
    def net_yes_shares(self) -> float:
        return self.yes.shares - self.no.shares

    @property
    def open_cost_usdc(self) -> float:
        """Cost basis of current inventory (both sides)."""
        return self.yes.shares * self.yes.avg_price + self.no.shares * self.no.avg_price

    @property
    def canonical_entry_price(self) -> float | None:
        """Avg entry in Yes-equivalent terms of the net side (chase guard, S1)."""
        net = self.net_yes_shares
        if abs(net) < 1e-9:
            return None
        if net > 0:
            return self.yes.avg_price if self.yes.shares > 0 else None
        return (1.0 - self.no.avg_price) if self.no.shares > 0 else None

    @property
    def realized_pnl(self) -> float:
        return self.yes.realized_pnl + self.no.realized_pnl

    def value_at(self, price_yes: float) -> float:
        return self.yes.shares * price_yes + self.no.shares * (1.0 - price_yes)

    def unrealized_pnl_at(self, price_yes: float) -> float:
        return self.value_at(price_yes) - self.open_cost_usdc


@dataclass
class WalletBook:
    """All of one wallet's condition positions."""

    conditions: dict[str, ConditionPosition] = field(default_factory=dict)

    def condition(self, condition_id: str) -> ConditionPosition:
        pos = self.conditions.get(condition_id)
        if pos is None:
            pos = self.conditions[condition_id] = ConditionPosition()
        return pos

    def apply_fill(
        self, condition_id: str, *, is_yes: bool, bought: bool, shares: float, usdc: float
    ) -> None:
        self.condition(condition_id).apply_fill(
            is_yes=is_yes, bought=bought, shares=shares, usdc=usdc
        )

    def total_realized_pnl(self) -> float:
        return sum(c.realized_pnl for c in self.conditions.values())
