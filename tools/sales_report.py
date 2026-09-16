"""Почему нет продаж: разбор по неделям. Только чтение, ничего не меняет.

    python tools/sales_report.py --db /root/flask-site/data/ikk_bot.db

Главное, что ищем:
  * счета создают, но не оплачивают  → сломана или отпугивает касса;
  * счета не создают вовсе          → до оплаты не доходят, дело в воронке;
  * подписки живут на бесплатных днях → платить незачем, раздали слишком много.
"""
import argparse
import sqlite3
import time
from datetime import datetime

WEEK = 7 * 86400


def week_start(ts):
    d = datetime.fromtimestamp(ts)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp()) - d.weekday() * 86400


def main():
    ap = argparse.ArgumentParser(description="Разбор продаж IKK VPN")
    ap.add_argument("--db", default="/root/flask-site/data/ikk_bot.db")
    ap.add_argument("--weeks", type=int, default=10)
    # Свои тестовые счета портят картину: владелец создаёт их, чтобы
    # посмотреть кассу, и они всегда остаются неоплаченными.
    ap.add_argument("--skip", default="", help="id через запятую: чьи счета не считать")
    args = ap.parse_args()
    skip = [int(x) for x in args.skip.replace(" ", "").split(",") if x]
    SKIP = "" if not skip else " and user_id not in (%s)" % ",".join(map(str, skip))

    now = int(time.time())
    since = week_start(now) - (args.weeks - 1) * WEEK
    c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    q = lambda sql, *a: c.execute(sql, a).fetchall()
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]

    def by_week(sql, *a):
        out = {}
        for r in q(sql, *a):
            out[week_start(r["created_at"])] = out.get(week_start(r["created_at"]), 0) + 1
        return out

    new = by_week("select created_at from users where created_at>=?", since)
    inv = by_week("select created_at from bot_invoices where created_at>=?" + SKIP, since)
    paid = by_week("select created_at from bot_invoices "
                   "where status='paid' and created_at>=?" + SKIP, since)
    # В payments бот пишет и оплаты картой (stars=0), поэтому звёзды —
    # только те, где stars>0. Иначе столбец дублирует «оплачено».
    stars = by_week("select created_at from payments where stars>0 and created_at>=?", since)
    promo = {}
    for r in q("select used_at from promo_uses where used_at>=?", since):
        k = week_start(r["used_at"])
        promo[k] = promo.get(k, 0) + 1

    print("=" * 74)
    print("ПРОДАЖИ ПО НЕДЕЛЯМ")
    print("=" * 74)
    print(f"{'неделя':<12}{'новых':>7}{'счетов':>8}{'оплачено':>10}{'звёзды':>8}"
          f"{'промокоды':>11}{'выручка ₽':>11}")
    for i in range(args.weeks):
        w = since + i * WEEK
        rub = one("select coalesce(sum(amount_rub),0) from bot_invoices "
                  "where status='paid' and created_at>=? and created_at<?" + SKIP, w, w + WEEK)
        label = datetime.fromtimestamp(w).strftime("%d.%m")
        print(f"{label:<12}{new.get(w,0):>7}{inv.get(w,0):>8}{paid.get(w,0):>10}"
              f"{stars.get(w,0):>8}{promo.get(w,0):>11}{rub:>11}")

    print()
    print("=" * 74)
    print("ВЫРУЧКА ПО МЕСЯЦАМ (бот + сайт)")
    print("=" * 74)
    print(f"{'месяц':<10}{'бот, ₽':>9}{'оплат':>7}{'сайт, ₽':>10}{'оплат':>7}"
          f"{'звёзды':>9}{'итого ₽':>10}")
    months = {}
    for r in q("select created_at, amount_rub from bot_invoices where status='paid'" +
               SKIP.replace("user_id", "user_id")):
        k = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m")
        m = months.setdefault(k, dict(bot=0, botn=0, web=0, webn=0, stars=0))
        m["bot"] += r["amount_rub"] or 0
        m["botn"] += 1
    for r in q("select created_at, amount_rub from web_payments "
               "where status in ('CONFIRMED','paid')"):
        k = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m")
        m = months.setdefault(k, dict(bot=0, botn=0, web=0, webn=0, stars=0))
        m["web"] += r["amount_rub"] or 0
        m["webn"] += 1
    for r in q("select created_at, stars from payments where stars>0"):
        k = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m")
        m = months.setdefault(k, dict(bot=0, botn=0, web=0, webn=0, stars=0))
        m["stars"] += r["stars"] or 0
    for k in sorted(months)[-6:]:
        m = months[k]
        print(f"{k:<10}{m['bot']:>9}{m['botn']:>7}{m['web']:>10}{m['webn']:>7}"
              f"{m['stars']:>9}{m['bot'] + m['web']:>10}")
    month_ago = now - 30 * 86400
    b30 = one("select coalesce(sum(amount_rub),0) from bot_invoices "
              "where status='paid' and created_at>=?" + SKIP, month_ago)
    w30 = one("select coalesce(sum(amount_rub),0) from web_payments "
              "where status in ('CONFIRMED','paid') and created_at>=?", month_ago)
    s30 = one("select coalesce(sum(stars),0) from payments where stars>0 and created_at>=?", month_ago)
    print(f"\n  За последние 30 дней: {b30 + w30} ₽" + (f" и {s30} ⭐" if s30 else ""))
    print("  Это оборот до комиссии кассы и без вычета серверов.")

    print()
    print("=" * 74)
    print("ОПЛАТА: ГДЕ ТЕРЯЕМ")
    print("=" * 74)
    for r in q("select provider, status, count(*) n, coalesce(sum(amount_rub),0) rub "
               "from bot_invoices where created_at>=?" + SKIP +
               " group by provider, status order by provider, status", since):
        print(f"  {r['provider'] or '(не указан)':<12} {r['status']:<10} {r['n']:>4} шт. {r['rub']:>8} ₽")
    tot = one("select count(*) from bot_invoices where created_at>=?" + SKIP, since)
    ok = one("select count(*) from bot_invoices "
             "where status='paid' and created_at>=?" + SKIP, since)
    if tot:
        print(f"\n  Доходят до оплаты: {ok} из {tot} счетов ({ok * 100 // tot}%)")
        print("  Если процент упал почти до нуля — проблема в кассе, а не в людях.")
    else:
        print("\n  Счетов не создавали вовсе — до экрана оплаты люди не доходят.")

    rows = q("select b.user_id uid, u.username, count(*) n from bot_invoices b "
             "left join users u on u.user_id=b.user_id "
             "where b.status!='paid' and b.created_at>=?" +
             SKIP.replace("user_id", "b.user_id") +
             " group by b.user_id order by n desc limit 5", since)
    if rows:
        print("\n  Кто чаще всех бросает счета неоплаченными:")
        for r in rows:
            who = f"@{r['username']}" if r["username"] else f"id{r['uid']}"
            print(f"    {who:<20} {r['n']} шт.  (id {r['uid']})")
        print("  Свой тестовый аккаунт уберите из счёта: --skip <id>")

    print()
    print("=" * 74)
    print("КТО СЕЙЧАС С ПОДПИСКОЙ")
    print("=" * 74)
    act = one("select count(*) from users where sub_until>?", now)
    payers = one("select count(distinct user_id) from ("
                 "select user_id from payments union all "
                 "select user_id from bot_invoices where status='paid')")
    act_payers = one("select count(*) from users u where u.sub_until>? and u.user_id in ("
                     "select user_id from payments union all "
                     "select user_id from bot_invoices where status='paid')", now)
    print(f"  Активных подписок: {act}")
    print(f"  Из них когда-либо платили: {act_payers}")
    print(f"  Живут на бесплатных днях: {act - act_payers}"
          f"  ({(act - act_payers) * 100 // act if act else 0}%)")
    print(f"  Платили хоть раз за всё время: {payers} человек")
    soon = one("select count(*) from users where sub_until>? and sub_until<?", now, now + 7 * 86400)
    print(f"  Заканчивается в ближайшие 7 дней: {soon} — это ближайшие возможные продажи")

    print()
    print("=" * 74)
    print("СКОЛЬКО РАЗДАЛИ БЕСПЛАТНО")
    print("=" * 74)
    print(f"  Активаций промокодов за период: {sum(promo.values())}")
    for r in q("select code, bonus_days, used_count from promo_codes order by used_count desc limit 6"):
        print(f"    {r['code']:<12} +{r['bonus_days']} дн. × {r['used_count']}")
    print(f"  Пробных периодов выдано всего: {one('select count(*) from users where trial_used=1')}")
    print(f"  Пришли по чужой ссылке (рефералы): {one('select count(*) from users where referred_by is not null')}")
    print()


if __name__ == "__main__":
    main()
