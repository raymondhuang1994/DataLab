"""Offline dashboards and full-data loopback dashboards sharing metric semantics."""
from __future__ import annotations
import hashlib
import html
import json
import math
from numbers import Real
import re
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import pandas as pd
from .io import load_frame
from .common import guard_output_path, json_safe
from .metrics import normalize_metric, legacy_metric, filter_frame, compute_metric, format_value
from .grouping import group_labels
from .quality import check_quality
from .safety import output_session, safe_write_text

ASSET = Path(__file__).resolve().parent / 'assets' / 'dashboard.html'
METRICS_ASSET = ASSET.with_name('dashboard_metrics.js')
RATE_NAME = re.compile(r'rate|ratio|percent|margin|yield|率|占比|比例|百分比', re.I)
DATE_NAME = re.compile(r'date|time|day|日期|时间', re.I)
AGGREGATIONS = {'sum', 'mean', 'median', 'count', 'count_rows', 'count_nonmissing', 'count_distinct'}
PAGE_SIZE = 50
MAX_SAFE_INTEGER = 9007199254740991


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _safe_json(value):
    return (json.dumps(json_safe(value), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def _signature(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _category_key(value):
    if pd.isna(value):
        return '__missing__'
    value = json_safe(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool):
        kind = 'boolean'
    elif isinstance(value, (int, float)):
        kind = 'number'
    else:
        kind = 'string'
    return json.dumps([kind, json_safe(value)], ensure_ascii=False, separators=(',', ':'))


def _date_keys(series, timezone='UTC'):
    if pd.api.types.is_numeric_dtype(series.dtype):
        return pd.Series('', index=series.index)
    from .analysis import parse_dates
    parsed = parse_dates(series, timezone)
    return parsed.dt.strftime('%Y-%m-%d').fillna('')


def _period_keys(series, timezone='UTC', freq='D'):
    from .analysis import parse_dates
    if freq not in {'D', 'W', 'M', 'Q', 'Y'}:
        raise ValueError('freq must be D, W, M, Q or Y')
    if pd.api.types.is_numeric_dtype(series.dtype):
        return pd.Series('', index=series.index), {}
    periods = parse_dates(series, timezone).dt.tz_localize(None).dt.to_period(freq)
    keys = periods.astype(str).where(periods.notna(), '')
    bounds = {str(p): {'start': p.start_time.strftime('%Y-%m-%d'), 'end': p.end_time.strftime('%Y-%m-%d')}
              for p in periods.dropna().unique()}
    return keys, bounds


def _records(frame):
    # pandas.to_json(double_precision=15) rounds tiny values before the browser
    # receives them (e.g. 1.4e-15 becomes 1e-15). Preserve Python floats and let
    # JSON's round-trip representation carry their full binary precision.
    # Normalize pandas NaT/NA before json_safe so date nulls remain real nulls.
    values = frame.astype(object).where(frame.notna(), None)
    return json_safe(values.to_dict(orient='records'))


def _prepare_frame(frame, *, date_column=None, metric=None, group=None, agg=None,
                   max_rows=50000, title=None, metric_definition=None, preferences=None, live=False, timezone='UTC', freq='D',
                   quality_rules=None, settings_provenance=None):
    quality = check_quality(frame, quality_rules)
    if quality['status'] == 'failed':
        raise ValueError('项目数据质量规则未通过：' + '；'.join(c['detail'] for c in quality['checks'] if c['status'] == 'failed'))
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        raise ValueError('max_rows must be a positive integer')
    if agg is not None and agg not in AGGREGATIONS:
        raise ValueError('Unknown dashboard aggregation')
    if freq not in {'D', 'W', 'M', 'Q', 'Y'}:
        raise ValueError('freq must be D, W, M, Q or Y')
    if not live and len(frame) > max_rows:
        raise ValueError(f'离线看板需要完整数据：{len(frame)} 行超过 max_rows={max_rows}；请先预聚合、提高上限或改用 serve。不会截取前几行计算总体。')
    for column in frame:
        if frame[column].map(lambda v: isinstance(v, Real) and not isinstance(v, bool) and math.isinf(v)).any():
            raise ValueError(f'字段 {column} 含实际数值 Infinity/-Infinity；请先明确处理非有限值。看板不会将它们转为空值而改变计数口径。')
        if frame[column].map(lambda v: isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v)
                             and abs(v) > MAX_SAFE_INTEGER and float(v).is_integer()).any():
            raise ValueError(f'字段 {column} 含超过 JavaScript 安全整数范围的数值；标识符请保留为字符串，数值指标请明确换算单位后再生成看板。')
    for name, value in (('date', date_column), ('metric', metric), ('group', group)):
        if value is not None and value not in frame.columns:
            raise ValueError(f'Unknown {name} column: {value}')
    group_display = {}
    group_errors = {}
    for column in frame:
        try:
            group_display[column] = group_labels(frame[column])
        except ValueError as exc:
            group_errors[column] = str(exc)
    if group is not None:
        # Explicit choices must fail instead of silently selecting another field.
        group_labels(frame[group])
    numeric = [c for c in frame if pd.api.types.is_numeric_dtype(frame[c]) and not pd.api.types.is_bool_dtype(frame[c])]
    dates = [c for c in frame if pd.api.types.is_datetime64_any_dtype(frame[c])]
    for column in frame:
        if column not in dates and DATE_NAME.search(column) and not pd.api.types.is_numeric_dtype(frame[column]):
            values = frame[column].dropna()
            if len(values) and (_date_keys(values, timezone) != '').mean() >= .8:
                dates.append(column)
    chosen_date = date_column if date_column is not None else next(iter(dates), '')
    chosen_metric = metric if metric is not None else next(iter(numeric), '')
    if agg == 'count_rows' or (agg == 'count' and metric is None):
        chosen_metric = ''
    categories = [c for c in frame if c in group_display and c not in numeric and c not in dates and frame[c].nunique(dropna=False) <= 100]
    chosen_group = group if group is not None else next(iter(categories), '')
    if chosen_date and chosen_date not in dates:
        dates.append(chosen_date)
    locked = metric_definition is not None
    selected_agg = agg or ('mean' if RATE_NAME.search(chosen_metric) else 'sum')
    if not chosen_metric:
        selected_agg = 'count_rows'
    if locked:
        spec = normalize_metric(metric_definition)
        chosen_metric, selected_agg = spec.get('column') or '', spec['aggregation']
    else:
        spec = legacy_metric(column=chosen_metric or None, aggregation=selected_agg)
        selected_agg = spec['aggregation']
    preferences = preferences or {}
    if spec.get('precision') is None:
        spec = {**spec, 'precision': preferences.get('display', {}).get('precision', 2)}
    # Validate the metric, fields and registered filters before accepting a snapshot.
    compute_metric(frame, spec)
    date_keys, period_keys, period_bounds = {}, {}, {}
    if not live:
        for column in dates:
            date_keys[column] = _date_keys(frame[column], timezone).tolist()
            keys, bounds = _period_keys(frame[column], timezone, freq)
            period_keys[column] = keys.tolist()
            period_bounds.update(bounds)
    return {'records': _records(frame if not live else frame.iloc[:min(PAGE_SIZE, max_rows)]),
            'date_keys': date_keys, 'period_keys': period_keys, 'period_bounds': period_bounds,
            'group_labels': {c: values.tolist() for c, values in group_display.items()} if not live else {},
            'columns': [{'name': c, 'numeric': c in numeric, 'date': c in dates, 'rate': bool(RATE_NAME.search(c)),
                         'groupable': c in group_display, 'group_error': group_errors.get(c)} for c in frame],
            'rows_total': len(frame), 'rows_loaded': len(frame) if not live else min(PAGE_SIZE, max_rows, len(frame)),
            'truncated': False, 'detail_limit': max_rows, 'server_aggregation': live,
            'defaults': {'date': chosen_date, 'metric': chosen_metric, 'group': chosen_group, 'agg': selected_agg},
            'metric_definition': spec, 'metric_locked': locked, 'timezone': timezone, 'frequency': freq, 'preferences': preferences, 'title': title,
            'quality': quality, 'quality_rules': quality_rules or {}, 'settings_provenance': settings_provenance or {}}


class SourceStore:
    """Full source stays on the server; all aggregation includes every eligible row."""
    def __init__(self, input_path, *, sheet=None, interval=5, live=True, **options):
        self.path = Path(input_path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f'Source is not a file: {self.path}')
        self.sheet, self.live = sheet, live
        if not math.isfinite(float(interval)):
            raise ValueError('interval must be finite')
        self.interval = max(2.0, float(interval))
        self.options = options
        self.lock = threading.RLock()
        self._signature, self._frame = None, None
        self._snapshot = {'records': [], 'columns': [], 'rows_total': 0, 'rows_loaded': 0,
                          'truncated': False, 'server_aggregation': live, 'detail_limit': options.get('max_rows', 50000),
                          'defaults': {'date': '', 'metric': '', 'group': '', 'agg': 'count_rows'},
                          'metric_locked': False, 'metric_definition': {'aggregation': 'count_rows'},
                          'preferences': options.get('preferences') or {}, 'title': options.get('title') or self.path.stem}
        self.last_checked = self.last_success = self.last_changed = self.error = None
        self.source = {'file_name': self.path.name, 'sha256': None}
        self.refresh(force=True)

    def refresh(self, force=False):
        with self.lock:
            self.last_checked = _now()
            try:
                signature = _signature(self.path)
                if not force and signature == self._signature and self.error is None:
                    return False
                digest = _sha256(self.path)
                frame = load_frame(self.path, sheet=self.sheet)
                prepared = _prepare_frame(frame, live=self.live, **self.options)
                if _signature(self.path) != signature or _sha256(self.path) != digest:
                    raise ValueError('Source changed during reading; retrying on the next check')
                changed = digest != self.source.get('sha256')
                self._frame, self._snapshot = frame, prepared
                self._snapshot['title'] = prepared['title'] or self.path.stem
                self.source = {'file_name': self.path.name, 'sha256': digest, 'size_bytes': signature[1],
                               'modified_at': datetime.fromtimestamp(signature[0] / 1e9, timezone.utc).isoformat(timespec='seconds')}
                self._signature, self.last_success = signature, _now()
                if changed:
                    self.last_changed = self.last_success
                self.error = None
                return changed
            except Exception as exc:
                self.error = f'{type(exc).__name__}: {exc}'.replace(str(self.path), self.path.name)[:500]
                return False

    def _status(self):
        return {'source': dict(self.source),
                'protection': {'mode': 'software_guards', 'source_access': 'read_only',
                               'external_source_updates_allowed': self.live,
                               'integrity_scope': 'each_snapshot_read', 'os_isolation_verified': False},
                'status': {'last_checked': self.last_checked, 'last_success': self.last_success,
                'last_changed': self.last_changed, 'stale': self.error is not None, 'error': self.error, 'interval_seconds': self.interval}}

    def snapshot(self):
        with self.lock:
            if self.live and self._frame is not None:
                return self.query({})
            return {**self._snapshot, **self._status()}

    def query(self, query):
        with self.lock:
            if not isinstance(query, dict) or set(query) - {'metric', 'agg', 'group', 'category', 'date', 'start', 'end', 'page'}:
                raise ValueError('Only metric, agg, group, category, date, start, end and page controls are accepted')
            state = {**self._snapshot['defaults'], 'category': '', 'start': '', 'end': '', 'page': 0, **query}
            for key in ('metric', 'agg', 'group', 'category', 'date', 'start', 'end'):
                if not isinstance(state[key], str) or len(state[key]) > 1000:
                    raise ValueError(f'Invalid {key} control')
            if not isinstance(state['page'], int) or isinstance(state['page'], bool) or state['page'] < 0:
                raise ValueError('page must be a nonnegative integer')
            if self._frame is None:
                return {**self._snapshot, **self._status(), 'query': state, 'aggregate': None, 'trend': [], 'groups': [], 'categories': []}
            frame = self._frame
            for key in ('metric', 'group', 'date'):
                if state[key] and state[key] not in frame:
                    raise ValueError(f'Unknown {key} column')
            canonical_labels = group_labels(frame[state['group']]) if state['group'] else None
            if self._snapshot['metric_locked']:
                if state['metric'] != self._snapshot['defaults']['metric'] or state['agg'] != self._snapshot['defaults']['agg']:
                    raise ValueError('注册指标的计算口径已锁定，不能改为其他聚合方式。')
                spec = self._snapshot['metric_definition']
            else:
                if state['agg'] not in AGGREGATIONS:
                    raise ValueError('Unsupported aggregation')
                spec = legacy_metric(column=state['metric'] or None, aggregation=state['agg'])
                spec['precision'] = self._snapshot['metric_definition'].get('precision', 2)
            for key in ('start', 'end'):
                if state[key] and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', state[key]) or _date_keys(pd.Series([state[key]])).iloc[0] != state[key]):
                    raise ValueError('Dates must be valid YYYY-MM-DD strings')
            if state['start'] and state['end'] and state['start'] > state['end']:
                raise ValueError('Start date must not be after end date')
            if (state['start'] or state['end']) and not state['date']:
                raise ValueError('Date bounds require a date column')
            eligible = filter_frame(frame, spec.get('filters', []))
            categories = []
            if state['group']:
                values = eligible[state['group']].drop_duplicates()
                categories = [{'value': _category_key(v), 'label': canonical_labels.loc[index]} for index, v in values.items()]
                categories.sort(key=lambda x: x['label'])
            mask = pd.Series(True, index=frame.index)
            dates = _date_keys(frame[state['date']], self._snapshot.get('timezone', 'UTC')) if state['date'] else None
            if state['start'] or state['end']:
                mask &= dates != ''
                if state['start']:
                    mask &= dates >= state['start']
                if state['end']:
                    mask &= dates <= state['end']
            context = frame.loc[mask]
            selected = context
            if state['group'] and state['category']:
                selected = context.loc[context[state['group']].map(_category_key) == state['category']]
            aggregate = compute_metric(selected, spec)
            detail = filter_frame(selected, spec.get('filters', []))
            trend, groups = [], []
            if state['date']:
                keys, bounds = _period_keys(selected[state['date']], self._snapshot.get('timezone', 'UTC'), self._snapshot.get('frequency', 'D'))
                for day, part in selected.loc[keys != ''].groupby(keys[keys != ''], sort=True):
                    result = compute_metric(part, spec)
                    trend.append({'key': day, 'label': day, 'value': result['value'],
                                  **bounds[day],
                                  'formatted_value': format_value(result['value'], spec, self._snapshot['preferences'])})
            if state['group']:
                keys = context[state['group']].map(_category_key)
                labels = {item['value']: item['label'] for item in categories}
                for key, part in context.groupby(keys, sort=False):
                    result = compute_metric(part, spec)
                    if result['eligible_rows']:
                        groups.append({'key': key, 'label': labels.get(key, key), 'value': result['value'],
                                       'formatted_value': format_value(result['value'], spec, self._snapshot['preferences'])})
                groups.sort(key=lambda x: -(x['value'] if x['value'] is not None else -float('inf')))
            limit = self._snapshot['detail_limit']
            detail_rows = min(len(detail), limit)
            page = min(state['page'], max(0, math.ceil(detail_rows / PAGE_SIZE) - 1))
            state['page'] = page
            records = _records(detail.iloc[page * PAGE_SIZE:min((page + 1) * PAGE_SIZE, detail_rows)])
            missing_dates = int((_date_keys(detail[state['date']], self._snapshot.get('timezone', 'UTC')) == '').sum()) if state['date'] else 0
            values = [aggregate['value']] + [p['value'] for p in trend + groups]
            precision_notice = ('聚合结果超过浏览器安全整数范围：数值标签由 Python 精确格式化；图形坐标为近似显示。'
                                if any(isinstance(v, Real) and abs(v) > MAX_SAFE_INTEGER for v in values) else '')
            return {**self._snapshot, **self._status(), 'records': records, 'rows_loaded': len(records),
                    'query': state, 'metric_definition': spec, 'aggregate': aggregate,
                    'formatted_value': format_value(aggregate['value'], spec, self._snapshot['preferences']), 'trend': trend, 'groups': groups,
                    'categories': categories, 'missing_dates': missing_dates, 'detail_total': len(detail),
                    'precision_notice': precision_notice,
                    'detail_available': detail_rows, 'detail_truncated': len(detail) > limit,
                    'page_size': PAGE_SIZE, 'aggregation_scope': 'all_eligible_rows'}


def _render_page(payload, live=False):
    nonce = secrets.token_urlsafe(24)
    page = ASSET.read_text(encoding='utf-8')
    page = page.replace('__METRICS_JS__', METRICS_ASSET.read_text(encoding='utf-8'))
    return (page.replace('__PAGE_TITLE__', html.escape(payload.get('title') or 'DataLab', quote=True))
            .replace('__SCRIPT_NONCE__', nonce).replace('__BOOTSTRAP_JSON__', _safe_json({'live': bool(live), 'data': payload})))


def _defaults(options, preferences):
    options = dict(options)
    prefs = preferences or {}
    options.setdefault('max_rows', prefs.get('dashboard', {}).get('max_rows', 50000))
    options['preferences'] = prefs
    return options


def build_dashboard(input_path, output_path, date_column=None, metric=None, group=None, sheet=None,
                    metric_definition=None, preferences=None, **options):
    files = options.get('settings_provenance', {}).get('files', {})
    configs = [item['path'] for item in files.values() if isinstance(item, dict) and item.get('path')]
    with output_session(output_path, inputs=configs, raw_inputs=[input_path]):
        store = SourceStore(input_path, sheet=sheet, live=False, date_column=date_column, metric=metric, group=group,
                            metric_definition=metric_definition, **_defaults(options, preferences))
        payload = store.snapshot()
        if payload['status']['error']:
            raise ValueError(payload['status']['error'])
        output = guard_output_path(input_path, {}, output_path)
        safe_write_text(output, _render_page(payload), encoding='utf-8')
        return {'output_path': str(output), 'rows_total': payload['rows_total'], 'rows_loaded': payload['rows_loaded'],
                'truncated': False, 'source_sha256': payload['source']['sha256'], 'protection': payload['protection']}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, store):
        self.store, self.stop_polling, self.poll_thread = store, threading.Event(), None
        super().__init__(address, DashboardHandler)
    def serve_forever(self, poll_interval=.25):
        def watch():
            while not self.stop_polling.wait(self.store.interval):
                self.store.refresh()
        self.poll_thread = threading.Thread(target=watch, name='datalab-source-poll', daemon=True)
        self.poll_thread.start()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self.stop_polling.set()
            self.poll_thread.join(timeout=5)
    def server_close(self):
        self.stop_polling.set()
        super().server_close()


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        pass
    def _allowed(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        if self.headers.get('Host', '').lower() not in hosts or (origin and origin not in {f'http://{h}' for h in hosts}):
            self.send_error(403, 'Only same-origin loopback requests are allowed')
            return False
        return True
    def _send(self, payload, content_type='application/json; charset=utf-8'):
        body = (payload if isinstance(payload, str) else _safe_json(payload)).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not self._allowed():
            return
        route = urlsplit(self.path)
        if route.path not in {'/', '/api/data'}:
            self.send_error(404)
            return
        if route.path == '/api/data' and route.query == 'refresh=1':
            self.server.store.refresh(force=True)
        payload = self.server.store.snapshot()
        self._send(_render_page(payload, live=True), 'text/html; charset=utf-8') if route.path == '/' else self._send(payload)
    def do_POST(self):
        if not self._allowed():
            return
        if urlsplit(self.path).path != '/api/query':
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= 16384 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('A bounded JSON request is required')
            query = json.loads(self.rfile.read(size))
            self._send(self.server.store.query(query))
        except (ValueError, TypeError, KeyError) as exc:
            self.send_error(400, str(exc).encode('ascii', 'replace').decode())


def create_dashboard_server(input_path, host='127.0.0.1', port=8765, interval=None,
                            date_column=None, metric=None, group=None, sheet=None,
                            metric_definition=None, preferences=None, **options):
    if host not in {'127.0.0.1', 'localhost'}:
        raise ValueError('Dashboard server only supports loopback: 127.0.0.1 or localhost')
    if interval is None:
        interval = (preferences or {}).get('dashboard', {}).get('interval', 5)
    store = SourceStore(input_path, sheet=sheet, interval=interval, live=True, date_column=date_column,
                        metric=metric, group=group, metric_definition=metric_definition, **_defaults(options, preferences))
    try:
        return DashboardServer((host, int(port)), store)
    except OSError as exc:
        raise OSError(f'Cannot bind the local dashboard on {host}:{port}; choose another --port. {exc}') from exc


def _common_options(args):
    from .settings import resolve_settings
    overrides = {key: getattr(args, key) for key in ('date', 'metric', 'group', 'sheet', 'agg', 'title', 'timezone', 'freq') if getattr(args, key) is not None}
    resolved = resolve_settings(project_path=args.project, preferences_path=args.preferences, metric_id=args.metric_id, overrides=overrides)
    analysis = resolved['analysis']
    options = {'date_column': analysis.get('date'), 'metric': analysis.get('metric'), 'group': analysis.get('group'),
               'sheet': analysis.get('sheet'), 'agg': analysis.get('agg') if args.agg is not None else None,
               'title': analysis.get('title'), 'timezone': analysis.get('timezone', 'UTC'),
               'freq': analysis.get('freq', 'M') if args.project or args.freq else 'D',
               'metric_definition': resolved['metric_definition'] if args.metric_id else None, 'preferences': resolved['preferences'],
               'quality_rules': resolved['quality'], 'settings_provenance': resolved['provenance']}
    if args.max_rows is not None:
        options['max_rows'] = args.max_rows
    return options


def _dashboard_command(args):
    with output_session(args.out, inputs=[p for p in (args.project, args.preferences) if p], raw_inputs=[args.input]):
        return build_dashboard(args.input, args.out, **_common_options(args))


def _serve_command(args):
    server = create_dashboard_server(args.input, port=args.port, interval=args.interval, **_common_options(args))
    print(f'DataLab dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
    print(f'Polling the local file every {server.store.interval:g} seconds. Stop with Ctrl+C.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def add_dashboard_parser(subparsers):
    for command, help_text, handler in (('dashboard', 'Export a complete offline HTML snapshot', _dashboard_command),
                                       ('serve', 'Serve a full-data dashboard refreshed from a local file', _serve_command)):
        parser = subparsers.add_parser(command, help=help_text)
        parser.add_argument('--input', required=True)
        for name in ('date', 'metric', 'group', 'sheet', 'title', 'timezone', 'project', 'preferences', 'metric-id'):
            parser.add_argument('--' + name)
        parser.add_argument('--agg', choices=sorted(AGGREGATIONS), default=None)
        parser.add_argument('--freq', choices=['D', 'W', 'M', 'Q', 'Y'], default=None)
        parser.add_argument('--max-rows', type=int, default=None, help='Offline full-data limit / live detail cap; default from preferences')
        if command == 'dashboard':
            parser.add_argument('--out', required=True)
        else:
            parser.add_argument('--port', type=int, default=8765)
            parser.add_argument('--interval', type=float, default=None, help='File check seconds; preferences default 5, minimum 2')
        parser.set_defaults(func=handler)
