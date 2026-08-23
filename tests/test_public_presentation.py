from types import SimpleNamespace

from presentation import public
from services.analytics import Dashboard
from services.bank import DickPayout, PisyagoResult


def test_contextual_lines_do_not_repeat_immediately(monkeypatch) -> None:
    public._recent.clear()
    monkeypatch.setattr(public.random, "choice", lambda rows: rows[0])

    first = public.choose_line("test", ("one", "two", "three"))
    second = public.choose_line("test", ("one", "two", "three"))

    assert first == "one"
    assert second == "two"


def test_dick_result_is_outcome_first_safe_and_only_shows_real_effects(monkeypatch) -> None:
    public._recent.clear()
    monkeypatch.setattr(public.random, "choice", lambda rows: rows[0])
    payout = DickPayout(nominal=12, credited=7, emitted=2, corporation_paid=5, clipped=5)
    view = public.DickView(
        user_id=7,
        name="<Хер & сын>",
        game_delta=12,
        size=27,
        rank=2,
        rank_before=5,
        payout=payout,
        garnished=3,
    )

    result = public.dick_result(view)

    assert result.startswith('🍆 <a href="tg://user?id=7">&lt;Хер &amp; сын&gt;</a>: <b>+12 см</b>')
    assert "Размер: <b>27 см</b> · место <b>#2</b> (↑3)" in result
    assert "Выдано 7/12" in result
    assert "Взыскано с прироста: 3" in result
    assert "Следующая попытка" not in result
    assert "ПИСЯГО" not in result


def test_dick_insurance_and_debt_remain_visible() -> None:
    result = public.dick_result(
        public.DickView(
            user_id=8,
            name="Игрок",
            game_delta=-20,
            size=4,
            rank=9,
            pisyago=PisyagoResult(
                loss=20,
                assets=4,
                coverage_pct=50,
                covered=10,
                debt=10,
                remaining=30,
                reset_at=123,
            ),
            debt=10,
            debt_due="20.08.2026 10:00",
        )
    )

    assert "ПИСЯГО покрыло 10/20" in result
    assert "Новый долг: 10 см до 20.08.2026 10:00" in result


def test_repeat_is_the_only_dick_message_with_timer() -> None:
    result = public.dick_repeat(9, "Ждун", 10, 3, "2 ч 4 мин", "")
    assert "Следующая попытка через <b>2 ч 4 мин</b>" in result


def test_duel_result_has_one_punch_and_material_accounting(monkeypatch) -> None:
    public._recent.clear()
    monkeypatch.setattr(public.random, "choice", lambda rows: rows[0])
    result = public.duel_result(
        public.DuelView(
            winner="Малыш <x>",
            loser="Гигант & Co",
            attacker="Гигант & Co",
            defender="Малыш <x>",
            attacker_before=100,
            attacker_after=80,
            defender_before=20,
            defender_after=34,
            stake=20,
            profit=14,
            tax=6,
            base_chance=0.8,
            final_chance=0.75,
            winner_was_attacker=False,
            reaction_seconds=5,
            garnished=4,
        )
    )

    assert "Малыш &lt;x&gt; победил Гигант &amp; Co" in result
    assert result.count("Букмекер рыдает") == 1
    assert "победителю +14 · налог 6" in result
    assert "С выигрыша взыскано 4" in result
    assert "Шанс победителя: 25%" in result


def test_profile_omits_empty_optional_rows_and_escapes_name() -> None:
    stats = SimpleNamespace(
        name="<name>",
        current_size=11,
        rank=2,
        net_worth=15,
        net_rank=1,
        total_grown=11,
        total_lost=0,
        best_day=5,
        worst_day=1,
        plays=3,
        days_played=3,
        duels_total=0,
        wins=0,
        losses=0,
        winrate=0.0,
        stolen_total=0,
        lost_in_duels=0,
        diseases_caught=0,
        public_label=None,
    )

    result = public.profile(stats, user_id=3)

    assert "&lt;name&gt;" in result
    assert "<b>Длина</b>" in result
    assert "<b>Состояние</b>" in result
    assert "<b>Игры</b>" in result
    assert "Дуэли:" not in result
    assert "Заражений:" not in result


def test_top_caption_keeps_top_three_and_largest_movement() -> None:
    data = Dashboard(
        title="Чат",
        section="leaders",
        period="7",
        metrics=[
            ("Участников", "4"),
            ("#1", "А: 30 см"),
            ("#2", "Б: 20 см"),
            ("#3", "В: 10 см"),
            ("#4", "Г: 5 см"),
        ],
        labels=["1", "2"],
        values=[10, 30],
        chart_title="Гонка",
        series=[("А <&>", [10, 30]), ("Б", [30, 25])],
    )

    result = public.top_caption(data)

    assert "#1 А: 30 см" in result
    assert "#3 В: 10 см" in result
    assert "#4" not in result
    assert "А &lt;&amp;&gt; +20 см" in result
