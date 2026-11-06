# -*- coding: utf-8 -*-
"""对照 templates/html_report_contract.yaml 检查各阶段 HTML 是否达标。

用法：
    python 检查HTML达标.py             # 检查当前目录（产品根 / run 目录）
    python 检查HTML达标.py <run目录>   # 指定产品根

同时兼容两种布局：
    V2   ：{板块夹}/github-tabler-html-report/HTML/xxx.html
    旧版 ：{板块夹}/github-tabler-html-report/HTML/xxx.html
找不到时再按文件名全盘递归兜底（排除插件与虚拟环境目录）。
"""
import io, os, re, sys, importlib.util, json
from html.parser import HTMLParser
from pathlib import Path

# (名称, 文件名, V2 板块夹, 下限, 07-26 基准, 上限)
# S2 使用固定模板/清单校验，不再要求体积、图表或折叠数量。
SPEC = [
    ("S1 综合可行性", "阶段_S1_选品可行性研究报告.html", "S1_选品板块",
     {"size_kb": 70, "section": 30, "table": 24, "svg": 3, "kpi": 5, "bar": 5},
     {"size_kb": 92, "section": 38, "table": 31, "svg": 3, "kpi": 7, "bar": 7}, {}),
    ("S2 词库·SIF", "06_SIF_产品词库.html", "S2_文案板块", {}, {}, {}),
    ("S2 词库·卖家精灵", "06_卖家精灵_产品词库.html", "S2_文案板块", {}, {}, {}),
    ("S2 词库·西柚", "06_西柚_产品词库.html", "S2_文案板块", {}, {}, {}),
    ("S2 Listing文案", "07_Listing文案.html", "S2_文案板块", {}, {}, {}),
    ("S3 视觉方案", "阶段_S3_视觉方案.html", "S3_视觉板块",
     {"size_kb": 60, "section": 15, "table": 10, "details": 3}, {}, {}),
    ("S4 广告架构", "阶段_S4_广告架构.html", "S4_广告板块",
     {"size_kb": 50, "section": 12, "table": 12, "bar": 8}, {}, {}),
]

PAT = {
    "section": r"<section",
    "table":   r"<table",
    "svg":     r"<svg",
    "details": r"<details",
    "kpi":     r'class="kpi|stat-card',
    "bar":     r"barrow|bar-fill|barfill",
}

THEME_CONTRACT = Path(__file__).resolve().parents[1] / 'templates/report_theme.json'


def theme_contract():
    return json.loads(THEME_CONTRACT.read_text(encoding='utf8'))


class _StyleParser(HTMLParser):
    """Classes are tokens; every table needs its own enclosing wrapper."""
    VOID={'meta','link','br','hr','img','input','source','wbr','area','base','embed','param','track','col'}
    def __init__(self, wrapper):
        super().__init__();self.classes=set();self.stack=[];self.wrapper=wrapper;self.unwrapped=0

    def handle_starttag(self,tag,attrs):
        classes=set(dict(attrs).get('class','').split());self.classes.update(classes)
        if tag=='table' and not any(self.wrapper in cs for _,cs in self.stack):self.unwrapped+=1
        if tag not in self.VOID:self.stack.append((tag,classes))

    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:];break


def style_errors(content):
    config=theme_contract();parser=_StyleParser(config['table_wrapper_class']);parser.feed(content)
    missing=[x for x in config['required_css_markers'] if x not in content]
    missing+=['class:'+x for x in config['required_classes'] if x not in parser.classes]
    if parser.unwrapped:missing.append('tablewrap: '+str(parser.unwrapped)+' 张表缺少包裹')
    banned=[x for x in config['forbidden_markers'] if x in content]
    # Declared theme fonts are one source for both generation and acceptance.
    allowed={font.strip().strip('\"\'').casefold() for stack in config['fonts'].values() for font in stack.split(',')}
    allowed.add('inherit')
    forbidden={font.casefold() for font in config.get('forbidden_fonts',[])}-allowed
    for declaration in re.findall(r'font-family\s*:\s*([^;}]+)',content,re.I):
        if 'var(' in declaration:continue
        for font in declaration.split(','):
            if font.strip().strip('\"\'').casefold() in forbidden:banned.append('font:'+font.strip())
    return missing,list(dict.fromkeys(banned))

SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "pw_user_data", "__pycache__",
             "skills", "mcp", "assets", "templates"}


class _CharsetParser(HTMLParser):
    """Inspect real meta elements, not examples inside comments or scripts."""
    def __init__(self):
        super().__init__()
        self.utf8 = False

    def handle_starttag(self, tag, attrs):
        if tag != 'meta':
            return
        attrs = dict(attrs)
        charset = (attrs.get('charset') or '').strip().lower()
        if not charset and (attrs.get('http-equiv') or '').lower() == 'content-type':
            match = re.search(r'charset\s*=\s*[\"\']?([^\s;\"\']+)', attrs.get('content') or '', re.I)
            charset = match.group(1).lower() if match else ''
        self.utf8 = self.utf8 or charset in {'utf-8', 'utf8'}


def has_early_utf8(path):
    """The complete UTF-8 declaration must fit within the first 1024 bytes."""
    with open(path, 'rb') as stream:
        prefix = stream.read(1024)
    parser = _CharsetParser()
    parser.feed(prefix.decode('utf-8-sig', errors='replace'))
    return parser.utf8


def locate(run, stage_dir, filename):
    """按 V2 → 旧版 → 递归兜底的顺序找文件。"""
    candidates = [
        os.path.join(run, stage_dir, "github-tabler-html-report", "HTML", filename),
        os.path.join(run, stage_dir, "github-tabler-html-report", filename),
        os.path.join(run, "生成报告", "HTML", filename),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    for base, dirs, files in os.walk(run):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if filename in files:
            return os.path.join(base, filename)
    return None


def measure(path):
    s = io.open(path, encoding="utf-8", errors="ignore").read()
    m = {"size_kb": os.path.getsize(path) // 1024, "charset_ok": has_early_utf8(path)}
    for k, p in PAT.items():
        m[k] = len(re.findall(p, s))
    missing,banned = style_errors(s)
    m["style_ok"] = (not missing) and (not banned)
    m["style_missing"] = missing
    m["style_banned"] = banned
    name = Path(path).name
    if 'name="starsky-report" content="r04-s4"' in s:
        spec=importlib.util.spec_from_file_location('_s4_html_report',Path(__file__).with_name('s4_launch.py'))
        native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
        checked=native.verify_report_file(path)
        m['field_gate']=True;m['verification_layer']='S4 current plan, sources, ordered business fields and native output hashes'
        m['style_ok']=m['style_ok'] and checked['ok'];m['style_missing']+=checked['errors']
    if name=='阶段_S3_视觉方案.html' and 'name="starsky-report" content="r03-s3"' in s:
        spec=importlib.util.spec_from_file_location('_s3_html_report',Path(__file__).with_name('s3_stage_report.py'))
        native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
        checked=native.verify_report_file(path)
        m['field_gate']=True
        m['verification_layer']='S3 current sources and native output hashes'
        m['style_ok']=m['style_ok'] and checked['ok']
        m['style_missing']+=checked['errors']
    if name=='阶段_S1_选品可行性研究报告.html' and 'data-report-version="2"' in s:
        from s1_report_v2 import inspect_html
        errors=inspect_html(s)
        m['style_ok']=not errors
        m['style_missing']=errors
        m['style_banned']=[]
        m['verification_layer']='S1 seven-chapter structured fields'
        m['field_gate']=True
    if name in {'06_SIF_产品词库.html', '06_卖家精灵_产品词库.html', '06_西柚_产品词库.html', '07_Listing文案.html', 'keyword_report.html', 'listing_report.html'} or 'data-template="s2-unified-' in s:
        spec = importlib.util.spec_from_file_location('s2_html_checker', Path(__file__).with_name('s2_report.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        checked = mod.verify_report_file(path)
        m['style_ok'] = checked['ok']
        m['style_missing'] = checked['errors']
        m['style_banned'] = []
        m['verification_layer'] = 'S2 fixed template and manifest'
    return m


def main():
    run = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    run = os.path.abspath(run)
    if not os.path.isdir(run):
        print(f"找不到 run 目录：{run}")
        return 1

    print(f"\nrun: {run}\n")
    allpass = True
    found_any = False

    for name, filename, stage_dir, mini, base, maxi in SPEC:
        p = locate(run, stage_dir, filename)
        if not p:
            print(f"  {name:16} —— 未生成\n")
            continue

        found_any = True
        got = measure(p)
        bad = [] if got.get('field_gate') else [k for k in mini if got.get(k, 0) < mini[k]]
        bad += [f"{k}(超上限)" for k in maxi if got.get(k, 0) > maxi[k]]
        if not got.get("style_ok"):
            bad.append("style")
        if not got.get("charset_ok"):
            bad.append("charset")
        flag = "PASS" if not bad else "FAIL"
        if bad:
            allpass = False

        print(f"  {name:16} [{flag}]  {os.path.relpath(p, run)}")
        if not got.get("charset_ok"):
            print('    charset: 前 1024 字节内缺少有效的 UTF-8 meta 声明')
        if not got.get("style_ok"):
            print("    未通过当前阶段模板校验 missing=%s banned=%s" % (
                got.get("style_missing"), got.get("style_banned")))
        print("    {:<10}{:>8}{:>8}{:>10}".format("指标", "实测", "下限", "07-26基准"))
        for k in mini:
            mark = "  <<<" if k in bad else ""
            print("    {:<10}{:>8}{:>8}{:>10}{}".format(
                k, got.get(k, 0), mini[k], base.get(k, "-"), mark))
        for k in maxi:
            mark = "  <<< 超上限" if f"{k}(超上限)" in bad else ""
            print("    {:<10}{:>8}  上限{:>4}{:>10}{}".format(
                k, got.get(k, 0), maxi[k], base.get(k, "-"), mark))
        print()

    print("=" * 46)
    if not found_any:
        print("一份 HTML 都没找到 —— 请确认传入的是产品根目录")
        print("=" * 46)
        return 1
    print("全部达标" if allpass else "存在未达标项 —— 按具体缺口修复后重新生成")
    print("=" * 46)
    return 0 if allpass else 1


if __name__ == "__main__":
    sys.exit(main())
