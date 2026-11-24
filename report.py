"""Allocation of contractor payments as of an inclusive reporting date."""
import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path


def money(value):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
        raise ValueError(f'Некорректная сумма: {value!r}')
    rub, _, kop = value.partition('.')
    return int(rub) * 100 + int(kop.ljust(2, '0'))


def fmt(n):
    return f'{"-" if n < 0 else ""}{abs(n)//100}.{abs(n)%100:02d}'


def day(value):
    return date.fromisoformat(value) if value else None


def require(condition, message):
    if not condition:
        raise ValueError(message)


SCHEMA = {
    'contracts': 'contract_id contractor amount signed_at',
    'works': 'work_id contract_id title price status completed_at cancelled_at',
    'payments': 'payment_id contract_id work_id purpose amount status created_at paid_at refunded_at',
}


def read_rows(folder, name):
    with (folder / f'{name}.csv').open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields = SCHEMA[name].split()
        require(reader.fieldnames == fields, f'{name}: неверный заголовок CSV')
        rows = {}
        for row in reader:
            require(None not in row and None not in row.values(), f'{name}: неверное число полей, строка {reader.line_num}')
            key = row[fields[0]]
            require(key and key not in rows, f'{name}: пустой или повторный ID {key!r}')
            rows[key] = row
        return rows


def load(folder):
    c, w, p = (read_rows(folder, n) for n in SCHEMA)
    for key, row in c.items():
        row['amount'] = money(row['amount'])
        row['signed_at'] = day(row['signed_at'])
        require(row['signed_at'], f'{key}: нет даты договора')
    for key, row in w.items():
        require(row['contract_id'] in c, f'{key}: неизвестный договор')
        row['price'] = money(row['price'])
        end, cancel = day(row['completed_at']), day(row['cancelled_at'])
        row['completed_at'], row['cancelled_at'] = end, cancel
        require((row['status'], bool(end), bool(cancel)) in {('done', True, False), ('planned', False, False), ('cancelled', False, True)}, f'{key}: статус не согласован с датами')
        event = end or cancel
        require(not event or event >= c[row['contract_id']]['signed_at'], f'{key}: событие до подписания')
    for key, row in p.items():
        cid, wid = row['contract_id'], row['work_id']
        require(cid in c, f'{key}: неизвестный договор')
        require(not wid or (wid in w and w[wid]['contract_id'] == cid), f'{key}: неверная привязка к работе')
        row['amount'] = money(row['amount'])
        created, paid, refund = (day(row[k]) for k in ('created_at', 'paid_at', 'refunded_at'))
        row.update(created_at=created, paid_at=paid, refunded_at=refund)
        require((row['status'], bool(paid), bool(refund)) in {('paid', True, False), ('queued', False, False), ('refunded', True, True)}, f'{key}: статус не согласован с датами')
        require(created and created >= c[cid]['signed_at'] and (not paid or paid >= created) and (not refund or refund >= paid), f'{key}: неверная последовательность дат')
    return c, w, p


def build(contracts, works, payments, as_of):
    details, totals = [], []
    for cid, contract in sorted(contracts.items()):
        if contract['signed_at'] > as_of:
            continue
        current = {}
        for wid, w in sorted(works.items()):
            if w['contract_id'] != cid:
                continue
            state = ('cancelled' if w['cancelled_at'] and w['cancelled_at'] <= as_of else
                     'done' if w['completed_at'] and w['completed_at'] <= as_of else 'planned')
            current[wid] = dict(contract_id=cid, work_id=wid, title=w['title'], status=state,
                                price=w['price'], effective_price=0 if state == 'cancelled' else w['price'],
                                direct_paid=0, advance_allocated=0)
        pool = sent = returned = queued = 0
        for p in payments.values():
            if p['contract_id'] != cid or p['created_at'] > as_of:
                continue
            if not p['paid_at'] or p['paid_at'] > as_of:
                queued += p['amount']
                continue
            sent += p['amount']
            if p['refunded_at'] and p['refunded_at'] <= as_of:
                returned += p['amount']
                continue
            if p['work_id']:
                current[p['work_id']]['direct_paid'] += p['amount']
            else:
                pool += p['amount']
        accepted = sorted((r for r in current.values() if r['status'] == 'done'),
                          key=lambda r: (works[r['work_id']]['completed_at'], r['work_id']))
        for r in accepted:
            amount = min(pool, max(0, r['effective_price'] - r['direct_paid']))
            r['advance_allocated'] = amount
            pool -= amount
        for r in current.values():
            r['paid'] = r['direct_paid'] + r['advance_allocated']
            r['remaining'] = max(0, r['effective_price'] - r['paid'])
            r['due_now'] = r['remaining'] if r['status'] == 'done' else 0
            r['excess'] = max(0, r['paid'] - r['effective_price'])
            details.append(r)
        def total(field):
            return sum(r[field] for r in current.values())
        net = sent - returned
        effective = total('effective_price')
        require(net == total('paid') + pool, f'{cid}: нарушен баланс денег')
        require(effective == total('paid') + total('remaining') - total('excess'), f'{cid}: нарушен баланс работ')
        totals.append(dict(contract_id=cid, contractor=contract['contractor'], contract_amount=contract['amount'],
                           listed_price=total('price'), effective_price=effective,
                           accepted_price=sum(r['price'] for r in accepted), gross_paid=sent, refunded=returned,
                           net_paid=net, allocated_paid=total('paid'), unallocated_advance=pool,
                           queued=queued, remaining=total('remaining'), due_now=total('due_now'),
                           excess=total('excess'), net_balance=effective-net))
    return details, totals


WORK_FIELDS = 'contract_id work_id title status price effective_price direct_paid advance_allocated paid remaining due_now excess'.split()
CONTRACT_FIELDS = 'contract_id contractor contract_amount listed_price effective_price accepted_price gross_paid refunded net_paid allocated_paid unallocated_advance queued remaining due_now excess net_balance'.split()


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=';')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: fmt(v) if isinstance(v, int) else v for k, v in row.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat, help='YYYY-MM-DD')
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--out', type=Path, default=Path('output'))
    args = parser.parse_args(argv)
    try:
        require(args.out.resolve() != args.data.resolve(), "Каталог вывода должен отличаться от входного")
        details, totals = build(*load(args.data), args.date)
        args.out.mkdir(parents=True, exist_ok=True)
        write_csv(args.out / 'works.csv', details, WORK_FIELDS)
        write_csv(args.out / 'contracts.csv', totals, CONTRACT_FIELDS)
    except (OSError, ValueError, csv.Error) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        return 1
    print(f'Отчёт на {args.date.isoformat()}: {len(details)} работ, {len(totals)} договоров. {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
