"""Deposit/loan rules in two tones (crude vs. dry-bank) + Telegraph publishing.

The rules must be openly readable, so we publish two Telegraph pages and store
their URLs on the Corporation row. ``/bank`` shows URL buttons to both. Re-running
publication overwrites the stored URLs (new pages). Telegraph needs no bot token —
it issues its own per-call account token.
"""

from __future__ import annotations

import aiohttp

from repositories import bank as repo

_API = "https://api.telegra.ph"


# Each entry is (tag, text). Tags: "h3"/"h4" headings, "p" paragraph, "li" bullet.
RULES_RUDE: list[tuple[str, str]] = [
    ("h3", "Правила Корпорации (по-пацански)"),
    ("p", "Читай внимательно, чтобы потом не скулил, что не знал."),
    ("h4", "Вклад"),
    (
        "li",
        "Закинул — всё, заморожено: этот кусок пропал из /top, в дуэль его не сунешь, а обычный прирост /dick к нему не прилипает.",
    ),
    (
        "li",
        "Процент капает ТОЛЬКО в те дни, когда ты реально доползаешь до /dick. Залёг на дно — вклад валяется дохлый.",
    ),
    (
        "li",
        "Ставка с каждым активным днём дохнет, халявного +1 нет, а весь навар упирается в установленный потолок. Мечтал разжиреть, лёжа пузом кверху? Обломись.",
    ),
    (
        "li",
        "Дёрнешь раньше срока — спалим весь накопленный процент и сверху отожмём штраф. Терпение, нищук.",
    ),
    (
        "li",
        "Иногда нагрянет «налоговая» и отгрызёт незастрахованный кусок. СЕКАСКО защищает до 40 см тела за отдельную премию, проценты ходят с голой задницей.",
    ),
    ("h4", "Кредит"),
    (
        "li",
        "Дадим в долг по твоему размеру и кредитной истории. Гасил по-человечески — дадим больше, кидал — сиди на бобах.",
    ),
    (
        "li",
        "Долг жиреет каждый божий день. Не вернёшь в срок — добро пожаловать в просрочку, терпила.",
    ),
    (
        "li",
        "В просрочке режем твой прирост с /dick и победы в дуэлях. И строчим в ЛС такие письма, что краснеть будешь.",
    ),
    ("h4", "Корпорация"),
    ("li", "У каждого чата своя касса: соседние нищеброды больше не жрут твои налоги и вклады."),
    (
        "li",
        "Первые 3 см положительного /dick печатаются явно, остальное платит свободная касса. Четверть вкладов лежит резервом.",
    ),
    (
        "li",
        "Не хватило на снятие — семь дней санации, затем незастрахованную часть вкладов пускают под секатор.",
    ),
]

RULES_STRICT: list[tuple[str, str]] = [
    ("h3", "Регламент обслуживания (официальная редакция)"),
    ("p", "Настоящий документ описывает условия размещения вкладов и предоставления кредитов."),
    ("h4", "1. Вклады"),
    (
        "li",
        "1.1. Сумма вклада списывается с ликвидного размера: ею нельзя платить в дуэлях или растить через /dick, но она входит в рейтинг чистого состояния.",
    ),
    (
        "li",
        "1.2. Проценты начисляются исключительно за дни активности владельца (использование команды /dick).",
    ),
    (
        "li",
        "1.3. Эффективная ставка убывает с каждым начисленным днём; дробный остаток переносится, а суммарный доход ограничен установленным потолком.",
    ),
    (
        "li",
        "1.4. При досрочном расторжении накопленные проценты аннулируются и удерживается штраф в пользу Корпорации.",
    ),
    (
        "li",
        "1.5. Конфискации применяются только к незастрахованному телу вклада. СЕКАСКО не защищает начисленные проценты.",
    ),
    ("h4", "2. Кредиты"),
    (
        "li",
        "2.1. Лимит кредитования определяется текущим размером заёмщика и его кредитной историей.",
    ),
    ("li", "2.2. На сумму задолженности ежедневно начисляются проценты."),
    ("li", "2.3. При нарушении срока возврата кредит признаётся просроченным."),
    (
        "li",
        "2.4. По просроченным обязательствам производится удержание из прироста /dick и из выигрышей в дуэлях; направляются уведомления.",
    ),
    ("h4", "3. Корпорация"),
    (
        "li",
        "3.1. Для каждого чата ведутся отдельные касса, обязательства, требования и страховой резерв.",
    ),
    (
        "li",
        "3.2. Не менее 25% обязательств резервируется. При нехватке ликвидности вводится семидневная санация и последующий bail-in незастрахованных требований.",
    ),
    (
        "li",
        "3.3. Первые 3 см положительного /dick являются контролируемой эмиссией; остальная выплата ограничена свободной кассой.",
    ),
]


def _to_nodes(rules: list[tuple[str, str]]) -> list:
    nodes = []
    for tag, text in rules:
        if tag == "li":
            nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [text]}]})
        else:
            nodes.append({"tag": tag, "children": [text]})
    return nodes


async def _create_page(session: aiohttp.ClientSession, title: str, nodes: list) -> str:
    async with session.get(
        f"{_API}/createAccount",
        params={"short_name": "Corp", "author_name": "Корпорация"},
    ) as resp:
        acc = await resp.json()
    token = acc["result"]["access_token"]

    import json

    async with session.post(
        f"{_API}/createPage",
        data={
            "access_token": token,
            "title": title,
            "author_name": "Корпорация",
            "content": json.dumps(nodes, ensure_ascii=False),
            "return_content": "false",
        },
    ) as resp:
        page = await resp.json()
    if not page.get("ok"):
        raise RuntimeError(f"Telegraph error: {page}")
    return page["result"]["url"]


async def publish() -> tuple[str, str]:
    """Publish both rule pages and persist their URLs on the Corporation row.
    Returns (rude_url, strict_url)."""
    async with aiohttp.ClientSession() as session:
        rude = await _create_page(session, "Правила банка Корпорации", _to_nodes(RULES_RUDE))
        strict = await _create_page(session, "Регламент банка Корпорации", _to_nodes(RULES_STRICT))
    await repo.set_rules_urls(rude, strict)
    return rude, strict
