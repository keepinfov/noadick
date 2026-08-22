from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import texts
from callbacks import BankCallback
from handlers import bank as handler
from services import bank


def _summary(*, deposit: bank.DepositView | None, sekasko: bank.SekaskoView) -> bank.BankSummary:
    return bank.BankSummary(
        size=10,
        deposit=deposit,
        sekasko=sekasko,
        loan=None,
        loans_repaid=0,
        loans_defaulted=0,
        loan_limit=0,
        pisyago=bank.PisyagoView(
            assets=10,
            threshold=20,
            coverage_pct=75,
            remaining=20,
            limit=20,
            reset_at=0,
        ),
    )


def _policy(**overrides: int) -> bank.SekaskoView:
    values = {
        "active": 64,
        "base_limit": 50,
        "base_protected": 50,
        "sekasko_protected": 64,
        "total_protected": 114,
        "risky": 6,
        "unused": 0,
        "configured_limit": 100,
        "purchase_limit": 70,
        "limit_available": 6,
        "available": 6,
        "premium_pct": 5,
        "available_premium": 1,
        "next_expires_at": 2_086_400,
        "next_expiring": 4,
    }
    values.update(overrides)
    return bank.SekaskoView(**values)


def _deposit() -> bank.DepositView:
    return bank.DepositView(
        principal=120,
        accrued=3,
        matures_at=2_000_000,
        matured=True,
        active_days=2,
        insured=64,
        insurance_expires_at=2_086_400,
    )


async def test_main_keyboard_has_dedicated_sekasko_screen(monkeypatch) -> None:
    monkeypatch.setattr(
        "repositories.bank.get_rules_corp",
        AsyncMock(return_value=SimpleNamespace(rules_url_rude="", rules_url_strict="")),
    )

    markup = await handler._main_kb(42)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert any(value and BankCallback.unpack(value).action == "sek" for value in callbacks)


def test_sekasko_keyboard_only_offers_affordable_coverage_and_shows_premium() -> None:
    markup = handler._sekasko_kb(
        9_223_372_036_854_775_807, _summary(deposit=_deposit(), sekasko=_policy())
    )
    buttons = [button for row in markup.inline_keyboard for button in row]
    purchase = [
        button
        for button in buttons
        if button.callback_data and BankCallback.unpack(button.callback_data).action == "sins"
    ]

    purchase_values: list[str] = []
    for button in purchase:
        assert button.callback_data is not None
        purchase_values.append(BankCallback.unpack(button.callback_data).value)
    assert purchase_values == ["5", "all"]
    assert all("премия 1" in button.text for button in purchase)
    for button in buttons:
        assert button.callback_data is not None
        assert len(button.callback_data.encode()) <= 64


def test_sekasko_screen_explains_coverage_expiry_and_uninsured_interest(monkeypatch) -> None:
    monkeypatch.setattr(texts, "_now_ts", lambda: 2_000_000)
    summary = _summary(
        deposit=_deposit(),
        sekasko=_policy(
            active=64,
            base_protected=50,
            sekasko_protected=64,
            total_protected=114,
            risky=6,
            next_expiring=4,
        ),
    )

    value = texts.bank_sekasko_screen(summary)

    assert "Активное покрытие: <b>64</b>" in value
    assert "Ближайшее уменьшение: −4" in value
    assert "Базовая защита без полиса: <b>50</b>" in value
    assert "Рисковое тело: <b>6</b>" in value
    assert "Начисленные проценты" in value
    assert "не защищаются никогда" in value
    assert "Сумма в кнопке — это новое покрытие, не цена" in value


def test_sekasko_screen_shows_active_coverage_without_deposit(monkeypatch) -> None:
    monkeypatch.setattr(texts, "_now_ts", lambda: 2_000_000)
    summary = _summary(
        deposit=None,
        sekasko=_policy(
            active=12,
            base_protected=0,
            sekasko_protected=0,
            total_protected=0,
            risky=0,
            unused=12,
            purchase_limit=0,
            limit_available=0,
            available=0,
            available_premium=0,
            next_expiring=12,
        ),
    )

    value = texts.bank_sekasko_screen(summary)
    markup = handler._sekasko_kb(42, summary)

    assert "Активное покрытие: <b>12</b>" in value
    assert "покрытие не пропадает" in value
    assert len(markup.inline_keyboard) == 1
    back_payload = markup.inline_keyboard[0][0].callback_data
    assert back_payload is not None
    assert BankCallback.unpack(back_payload).action == "home"


def test_deposit_inside_base_has_no_sekasko_purchase_buttons() -> None:
    deposit = bank.DepositView(
        principal=30,
        accrued=0,
        matures_at=2_000_000,
        matured=True,
        active_days=0,
    )
    summary = _summary(
        deposit=deposit,
        sekasko=_policy(
            active=0,
            base_protected=30,
            sekasko_protected=0,
            total_protected=30,
            risky=0,
            unused=0,
            purchase_limit=0,
            limit_available=0,
            available=0,
            available_premium=0,
            next_expires_at=0,
            next_expiring=0,
        ),
    )

    markup = handler._sekasko_kb(42, summary)
    value = texts.bank_sekasko_screen(summary)

    assert len(markup.inline_keyboard) == 1
    assert "Базовая защита без полиса: <b>30</b>" in value
    assert "Можно купить сейчас: <b>0</b>" in value


async def test_sekasko_screen_rejects_foreign_panel() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        answer=AsyncMock(),
    )
    callback_data = BankCallback(action="sek", user_id=42, value="_")

    await handler.cb_sekasko_screen(callback, callback_data)

    callback.answer.assert_awaited_once_with(texts.BANK_NOT_YOURS, show_alert=True)


def test_public_bail_in_notice_escapes_celebrated_initiator_and_stays_aggregate() -> None:
    value = texts.bank_bail_in_notice(
        "<Вася & Co>",
        wiped=70,
        payout=50,
        balance=-50,
        deficit=50,
    )

    assert "🏆 <b>&lt;Вася &amp; Co&gt;</b>" in value
    assert "<Вася & Co>" not in value
    assert "С вкладов суммарно списано" in value
    assert "персональные балансы" in value
