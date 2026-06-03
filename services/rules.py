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
    ("li", "Закинул — всё, заморожено: в /top тебя не видать, в дуэлю это не сунешь, от /dick оно ни на грамм не растёт."),
    ("li", "Процент капает ТОЛЬКО в те дни, когда ты реально доползаешь до /dick. Залёг на дно — вклад валяется дохлый."),
    ("li", "Ставка с каждым активным днём дохнет и упирается в потолок. Мечтал разжиреть, лёжа пузом кверху? Обломись."),
    ("li", "Дёрнешь раньше срока — спалим весь накопленный процент и сверху отожмём штраф. Терпение, нищук."),
    ("li", "Иногда нагрянет «налоговая» и молча отгрызёт кусок вклада. Не мы такие — жизнь такая, привыкай."),
    ("h4", "Кредит"),
    ("li", "Дадим в долг по твоему размеру и кредитной истории. Гасил по-человечески — дадим больше, кидал — сиди на бобах."),
    ("li", "Долг жиреет каждый божий день. Не вернёшь в срок — добро пожаловать в просрочку, терпила."),
    ("li", "В просрочке режем твой прирост с /dick и победы в дуэлях. И строчим в ЛС такие письма, что краснеть будешь."),
    ("h4", "Корпорация"),
    ("li", "Весь навар с налогов, процентов и штрафов со всех чатов стекается в один общий котёл."),
    ("li", "Из него же капают проценты по вкладам. Уйдёт касса в минус — это банкротство, и виноваты в нём вы, голодранцы."),
]

RULES_STRICT: list[tuple[str, str]] = [
    ("h3", "Регламент обслуживания (официальная редакция)"),
    ("p", "Настоящий документ описывает условия размещения вкладов и предоставления кредитов."),
    ("h4", "1. Вклады"),
    ("li", "1.1. Сумма вклада списывается с ликвидного баланса и не учитывается в рейтинге, дуэлях и ежедневном начислении /dick."),
    ("li", "1.2. Проценты начисляются исключительно за дни активности владельца (использование команды /dick)."),
    ("li", "1.3. Эффективная ставка убывает с каждым начисленным днём; суммарный доход ограничен установленным потолком."),
    ("li", "1.4. При досрочном расторжении накопленные проценты аннулируются и удерживается штраф в пользу Корпорации."),
    ("li", "1.5. Корпорация оставляет за собой право проведения списаний (конфискаций) в установленных пределах."),
    ("h4", "2. Кредиты"),
    ("li", "2.1. Лимит кредитования определяется текущим размером заёмщика и его кредитной историей."),
    ("li", "2.2. На сумму задолженности ежедневно начисляются проценты."),
    ("li", "2.3. При нарушении срока возврата кредит признаётся просроченным."),
    ("li", "2.4. По просроченным обязательствам производится удержание из прироста /dick и из выигрышей в дуэлях; направляются уведомления."),
    ("h4", "3. Корпорация"),
    ("li", "3.1. Все комиссии, проценты и штрафы со всех чатов аккумулируются на едином балансе Корпорации."),
    ("li", "3.2. За счёт указанного баланса выплачиваются проценты по вкладам; отрицательный баланс означает несостоятельность."),
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
