#!/usr/bin/env python3
"""Evaluate stored seasonal trends lazily; no scheduler, refresh or backend write."""
from __future__ import annotations

import argparse
import calendar
from datetime import date, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keyword_asset import _day, _schema, read_snapshot


def seasonal_reminders(series, *, today=None, lead_weeks=3, sku=None) -> list[dict]:
    return evaluate_rows(read_snapshot(series).seasonal_rows(sku), today=today, lead_weeks=lead_weeks)


def evaluate_rows(rows, *, today=None, lead_weeks=3) -> list[dict]:
    if lead_weeks not in range(3, 9):
        raise ValueError('lead_weeks must be 3 to 8')
    current = date.fromisoformat(_day(today))
    reminders = []
    for row in rows:
        season = row.get('seasonal') or {}
        if not any(tag in ['季节', '礼品', '节日', '礼品 / 节日', 'season', 'seasonal', 'gift'] for tag in row['semantic_tags']):
            continue
        source = season.get('source')
        if not isinstance(source, dict) or str(source.get('provider', '')).casefold() != 'sif' or source.get('tool') not in ['market_get_keyword_root_trend', 'market_get_keyword_history'] or not (source.get('sha256') or source.get('reference')):
            continue
        rises = season.get('rise_months') or []
        peaks = season.get('peak_months') or []
        if not rises or any(not isinstance(m, int) or m not in range(1, 13) for m in rises + peaks):
            continue
        cycles = []
        for year in [current.year - 1, current.year, current.year + 1]:
            for rise in rises:
                start = date(year, rise, 1)
                # Pair a rise with the first contiguous peak run before the
                # next rise. Independent seasons must not bridge each other.
                next_rise = min(((month - rise) % 12 for month in rises if month != rise), default=12)
                offsets = sorted({(month - rise) % 12 for month in peaks if (month - rise) % 12 < next_rise})
                end = None
                if offsets:
                    last = offsets[0]
                    for offset in offsets[1:]:
                        if offset != last + 1:
                            break
                        last = offset
                    end_year = year + (rise - 1 + last) // 12
                    end_month = (rise - 1 + last) % 12 + 1
                    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
                cycles.append((start, end))
        upcoming = sorted((s, e) for s, e in cycles if current <= s and current >= s - timedelta(weeks=lead_weeks))
        active = sorted((s, e) for s, e in cycles if e is not None and s <= current <= e)
        ended = sorted((s, e) for s, e in cycles if e is not None and e < current)
        if upcoming:
            start, end = upcoming[0]
            action, status = 'prepare', 'upcoming'
        elif active:
            start, end = active[-1]
            action, status = 'active', 'active'
        elif ended:
            start, end = ended[-1]
            # Removal is actionable only when this keyword was actually placed.
            # First month after peak also supplies an expiry signal to H.
            if not row['placements'] and (current - end).days > 31:
                continue
            action, status = 'remove', 'expired'
        else:
            continue
        volume = next((m for m in reversed(row['metrics']) if m.get('search_volume') is not None), {})
        reminders.append({'term': row['term'], 'marketplace': row['marketplace'], 'skus': row['skus'], 'holiday': season.get('holiday'), 'rise_date': start.isoformat(), 'peak_end': end.isoformat() if end else None, 'action': action, 'seasonal_status': status, 'source': source, 'search_volume': volume.get('search_volume'), 'volume_unit': volume.get('unit'), 'volume_source': volume.get('source'), 'suggested_positions': ['highlight', 'bullet_1', 'st', 'standard_aplus'], 'copy_fragments': season.get('copy_fragments') or {}, 'copy_gap': None if season.get('copy_fragments') else 'No reviewed factual snippet stored; generate through CT mapping and compliance scan.', 'ad_group_suggestion': {'theme': season.get('holiday') or row['term'], 'status': 'pending_review', 'action': 'pause seasonal theme' if action == 'remove' else 'review seasonal theme group'}, 'copy_allowed': action != 'remove' and row['permissions']['copy']['allowed'] in ['可写', '弱化表述'], 'requires_compliance_rescan': True, 'hooks': _schema()['seasonal_hooks']})
    return reminders


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', required=True)
    parser.add_argument('--today')
    parser.add_argument('--lead-weeks', type=int, default=3)
    args = parser.parse_args(argv)
    print(json.dumps(seasonal_reminders(args.series, today=args.today, lead_weeks=args.lead_weeks), ensure_ascii=False))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
