# -*- coding: utf-8 -*-
"""
Amazon 竞品图抓取器 —— 主图 / 副图 / A+ 一次抓完，按 ASIN 归档

用途：S3 视觉基准学习。替代手动用插件逐个下载的步骤。

用法：
    python asin_image_scraper.py B0CPYNF77V
    python asin_image_scraper.py B0CPYNF77V B079J4D965 B09ZTY1KDK
    python asin_image_scraper.py -f asins.txt          # 每行一个 ASIN
    python asin_image_scraper.py B0CPYNF77V -o D:/xxx/参考图_按ASIN
    python asin_image_scraper.py B0CPYNF77V -d co.uk   # 换站点

输出：
    参考图_按ASIN/
    └── B0CPYNF77V/
        ├── 主图/         main_01.jpg …（hiRes 原图）
        ├── A+/           aplus_01.jpg …
        ├── 页面信息.yaml  标题/品牌/价格/评分/评论数/上架日期
        └── 图片清单.csv   每张图的类型、原始URL、本地文件名

依赖：只用标准库 + requests。没有 requests 就 pip install requests
"""
import os, re, sys, csv, io, json, time, random, argparse
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit("缺少 requests，请先运行：pip install requests")

# 有些机器（公司代理 / 自签证书 / 缺 CA 包）会 SSL 校验失败。
# 这里只抓公开商品页的图片，不涉及任何账号或凭据，失败时自动降级并明确告知。
VERIFY = True


def _relax_ssl():
    global VERIFY
    if VERIFY:
        VERIFY = False
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        print("    ⚠️ 本机 SSL 证书校验失败，已降级为不校验证书继续抓取。")
        print("       （只抓公开商品页图片，不涉及账号；如需彻底解决："
              "pip install --upgrade certifi）")

# UA 池：每个会话随机取一个，被拦后换会话时一并换 UA。
# 单一固定 UA 的问题（2026-08 实测）：第一次抓成功，第二次同指纹直接被拦。
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
    "Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


def build_headers(ua):
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }


def new_session(domain="com", warm=True):
    """新会话 = 新 UA + 先逛一次首页拿 cookie。

    直接裸请求 /dp/ 是最容易被识别的模式 —— 真实浏览器总是带着
    session cookie 来的。预热一次首页，通过率明显提高。"""
    s = requests.Session()
    s.headers.update(build_headers(random.choice(UA_POOL)))
    if warm:
        try:
            s.get(f"https://www.amazon.{domain}/", timeout=20, verify=VERIFY)
            time.sleep(random.uniform(1.2, 2.5))
        except requests.exceptions.SSLError:
            _relax_ssl()
        except Exception:
            pass          # 预热失败不阻断，只是通过率下降
    return s


# 机器人页的判定特征（比单查 "captcha" 准：正常页脚也可能含这个词）
_BLOCK_MARKS = (
    "/errors/validatecaptcha",
    "api-services-support@amazon.com",
    "to discuss automated access",
    "type the characters you see in this image",
)


class Blocked(RuntimeError):
    """被亚马逊风控拦截（验证码页 / 503）"""


def _blocked(r):
    if r.status_code in (503, 429):
        return True
    head = r.text[:6000].lower()
    return any(m in head for m in _BLOCK_MARKS)


# ────────────────────────── 抓页面 ──────────────────────────
def fetch(asin, domain, sess, retry=2):
    """用主流程传入的会话抓页面（v1.1 前的 bug：这里自建裸 Session，
    预热和 cookie 全部作废）。被拦时指数退避 + 换会话换 UA 再试。"""
    url = f"https://www.amazon.{domain}/dp/{asin}"
    sess.headers["Referer"] = f"https://www.amazon.{domain}/"
    last = None
    for i in range(retry + 1):
        try:
            r = sess.get(url, timeout=30, verify=VERIFY)
        except requests.exceptions.SSLError:
            _relax_ssl()
            r = sess.get(url, timeout=30, verify=VERIFY)
        last = r
        if r.status_code == 200 and not _blocked(r):
            return r.text, url, sess
        if not _blocked(r) and r.status_code == 404:
            raise RuntimeError(f"{asin} 不存在（HTTP 404），检查 ASIN 或站点 -d 参数")
        if i < retry:
            # 3 秒重试是撞墙 —— 风控窗口是分钟级的。退避：约 20s → 75s
            w = random.uniform(15, 25) * (3 ** i)
            print(f"    ⏳ 被拦（HTTP {r.status_code}），等 {w:.0f}s 换个会话再试 "
                  f"（{i + 1}/{retry}）…")
            time.sleep(w)
            sess = new_session(domain)
            sess.headers["Referer"] = f"https://www.amazon.{domain}/"
    raise Blocked(
        f"{asin} 连续 {retry + 1} 次被拦（HTTP {last.status_code}）。")


# ────────────────────────── 解析 ──────────────────────────
def parse_main_images(html):
    """主图组藏在 ImageBlockATF 的 colorImages.initial 里，取 hiRes 原图"""
    out = []
    m = re.search(r"'colorImages'\s*:\s*\{\s*'initial'\s*:\s*(\[.*?\])\s*\}", html, re.S)
    if not m:
        m = re.search(r'"colorImages"\s*:\s*\{\s*"initial"\s*:\s*(\[.*?\])\s*\}', html, re.S)
    if m:
        blob = m.group(1)
        for item in re.finditer(r'\{(.*?)\}', blob, re.S):
            seg = item.group(1)
            hi = re.search(r'"hiRes"\s*:\s*"([^"]+)"', seg)
            lg = re.search(r'"large"\s*:\s*"([^"]+)"', seg)
            var = re.search(r'"variant"\s*:\s*"([^"]+)"', seg)
            u = (hi.group(1) if hi and hi.group(1) != "null" else
                 lg.group(1) if lg else None)
            if u:
                out.append((u, (var.group(1) if var else "")))
    # 兜底：data-a-dynamic-image
    if not out:
        for m2 in re.finditer(r'data-a-dynamic-image="([^"]+)"', html):
            try:
                d = json.loads(m2.group(1).replace("&quot;", '"'))
                out += [(u, "") for u in d.keys()]
            except Exception:
                pass
    # 去重保序
    seen, uniq = set(), []
    for u, v in out:
        k = re.sub(r'\._[^.]+_\.', '.', u)     # 去掉尺寸后缀再比
        if k not in seen:
            seen.add(k); uniq.append((u, v))
    return uniq


def parse_aplus_images(html):
    """只取真正的 A+ 图。

    关键判据：A+ 素材托管在专属路径下 ——
        m.media-amazon.com/images/S/aplus-media/...
        m.media-amazon.com/images/S/aplus-media-library-service-media/...
    而 /images/I/ 是通用路径，商品图、**评论图**、相关商品图都在那儿。
    实测：不加这条过滤，27 张里有 21 张是评论图和 related products。
    """
    out = []
    for m in re.finditer(
            r'(?:data-src|src|data-a-hires)="(https://[^"]*?/images/S/'
            r'(?:aplus-media|aplus-seller-content-images)[^"]+?'
            r'\.(?:jpg|jpeg|png))"', html):
        out.append(m.group(1))

    # 兜底：确实有少量卖家把 A+ 图挂在 /images/I/ 下，
    # 此时只从 A+ 容器内取，且必须能界定容器边界（不得贪婪到 body 尾）
    if not out:
        for cid in ("aplus_feature_div", "aplus", "aplusBrandStory_feature_div"):
            i = html.find(f'id="{cid}"')
            if i < 0:
                continue
            # 边界：下一个已知区块的起点
            stops = [html.find(x, i) for x in (
                'id="reviewsMedley"', 'id="customer-reviews"',
                'id="customerVideosContainer"', 'id="HLCXComparisonTable"',
                'id="similarities_feature_div"', 'id="sp_detail"',
                'id="dp-ads-center-promo', '</body')]
            stops = [x for x in stops if x > i]
            blk = html[i:min(stops) if stops else i + 400000]
            for m in re.finditer(r'(?:data-src|src)="(https://[^"]+?\.(?:jpg|jpeg|png))"', blk):
                out.append(m.group(1))

    bad = ("sprite", "grey-pixel", "transparent-pixel", "1x1", "loading-",
           "play-icon", "-icon", "/G/01/", "nav-sprite", "gno/")
    seen, uniq = set(), []
    for u in out:
        if any(x in u for x in bad):
            continue
        k = re.sub(r'\._[^.]+_\.', '.', u)
        if k not in seen:
            seen.add(k); uniq.append(u)
    return uniq


def parse_facts(html):
    def one(pat, flags=re.S):
        m = re.search(pat, html, flags)
        return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ""
    return {
        "title":    one(r'id="productTitle"[^>]*>(.*?)</span>'),
        "brand":    one(r'id="bylineInfo"[^>]*>([^<]{1,80})</a>'),
        "price":    one(r'class="a-offscreen"[^>]*>(.*?)</span>'),
        "rating":   one(r'([\d.]+)\s*out of 5 stars'),
        "reviews":  one(r'acrCustomerReviewText[^>]*>\s*([\d,]+)\s*rating'),
        "rank":     one(r'Best Sellers Rank[^#]*#([\d,]+)'),
        "date_first_available": one(r'Date First Available.*?<td[^>]*>\s*([A-Za-z0-9,\s]{6,30})\s*<'),
    }


# ────────────────────────── 下载 ──────────────────────────
def upsize(u):
    """把缩略图 URL 还原成原图：去掉 ._AC_SX679_. 这类尺寸段"""
    return re.sub(r'\._[A-Z0-9_,]+_\.', '.', u)


def download(url, path, sess, min_kb=0):
    try:
        try:
            r = sess.get(url, timeout=30, verify=VERIFY)
        except requests.exceptions.SSLError:
            _relax_ssl()
            r = sess.get(url, timeout=30, verify=VERIFY)
        if r.status_code == 200 and len(r.content) > max(3000, min_kb * 1024):
            io.open(path, "wb").write(r.content)
            return len(r.content)
    except Exception:
        pass
    return 0



# ────────────────────────── A+ 长图 ──────────────────────────
def stitch_aplus(aplus_dir, out_png, max_h=16000):
    """把 A+ 模块图按顺序纵向拼成一张长图。

    为什么不用浏览器截图：Amazon 对 headless 检测很严，会返回首页而不是商品页。
    而 A+ 模块图在页面上本来就是**纵向依序排列**的，按 aplus_01..N 顺序拼起来，
    得到的就是完整的 A+ 视觉叙事 —— 不联网、不被检测、不掉样式。

    散图看不出「他怎么组织信息」，长图才能一眼看完整段叙事。
    """
    try:
        from PIL import Image
    except ImportError:
        return None, "未安装 Pillow（pip install pillow）"

    files = sorted(f for f in os.listdir(aplus_dir)
                   if re.match(r"aplus_\d+\.(jpg|jpeg|png)$", f, re.I))
    if not files:
        return None, "没有 A+ 图可拼"

    imgs = []
    for f in files:
        try:
            im = Image.open(os.path.join(aplus_dir, f)).convert("RGB")
            imgs.append(im)
        except Exception:
            pass
    if not imgs:
        return None, "A+ 图无法读取"

    W = min(max(im.width for im in imgs), 2000)      # 宽度封顶 2000，够看又不臃肿
    GAP = 12
    norm = []
    for im in imgs:
        if im.width != W:
            im = im.resize((W, max(1, int(im.height * W / im.width))), Image.LANCZOS)
        norm.append(im)

    # 超过 max_h 就分段，避免生成看图工具打不开的超高图
    parts, cur, h = [], [], 0
    for im in norm:
        if cur and h + im.height + GAP > max_h:
            parts.append(cur); cur, h = [], 0
        cur.append(im); h += im.height + GAP
    if cur:
        parts.append(cur)

    base, ext = os.path.splitext(out_png)
    saved, total_h = [], 0
    for idx, grp in enumerate(parts, 1):
        H = sum(im.height for im in grp) + GAP * (len(grp) - 1)
        cv = Image.new("RGB", (W, H), (255, 255, 255))
        y = 0
        for im in grp:
            cv.paste(im, (0, y)); y += im.height + GAP
        path = out_png if len(parts) == 1 else f"{base}_{idx}of{len(parts)}{ext}"
        cv.save(path, quality=88, optimize=True)
        saved.append(path); total_h += H
    return (W, total_h, len(imgs), saved), None


# ────────────────────────── 主流程 ──────────────────────────
def already_done(base):
    """断点续抓判定：清单在 + 主图至少 1 张，就算抓过"""
    inv = os.path.join(base, "图片清单.csv")
    md = os.path.join(base, "主图")
    if not (os.path.isfile(inv) and os.path.isdir(md)):
        return None
    mains = [f for f in os.listdir(md) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    if not mains:
        return None
    ad = os.path.join(base, "A+")
    apl = [f for f in os.listdir(ad)
           if f.lower().endswith((".jpg", ".jpeg", ".png"))] if os.path.isdir(ad) else []
    return len(mains), len(apl)


def run_one(asin, outroot, domain, sess, aplus_min_kb=50, snap=False, force=False):
    base = os.path.join(outroot, asin)
    if not force:
        done = already_done(base)
        if done:
            print(f"\n[{asin}] 已抓过（主图 {done[0]} · A+ {done[1]}），跳过。"
                  f"要重抓加 --force")
            return done[0], done[1], sess

    print(f"\n[{asin}] 抓取中 …")
    html, url, sess = fetch(asin, domain, sess)

    mains = parse_main_images(html)
    aplus = parse_aplus_images(html)
    facts = parse_facts(html)

    os.makedirs(os.path.join(base, "主图"), exist_ok=True)
    os.makedirs(os.path.join(base, "A+"), exist_ok=True)

    rows, ok_m, ok_a = [], 0, 0
    for i, (u, var) in enumerate(mains, 1):
        u2 = upsize(u)
        fn = f"main_{i:02d}.jpg"
        n = download(u2, os.path.join(base, "主图", fn), sess)
        if n:
            ok_m += 1
            rows.append({"类型": "主图", "序号": i, "变体": var,
                         "文件": f"主图/{fn}", "大小KB": n // 1024, "原始URL": u2})
        time.sleep(random.uniform(.6, 1.3))

    for i, u in enumerate(aplus, 1):
        u2 = upsize(u)
        fn = f"aplus_{i:02d}.jpg"
        n = download(u2, os.path.join(base, "A+", fn), sess, min_kb=aplus_min_kb)
        if n:
            ok_a += 1
            rows.append({"类型": "A+", "序号": i, "变体": "",
                         "文件": f"A+/{fn}", "大小KB": n // 1024, "原始URL": u2})
        time.sleep(random.uniform(.6, 1.3))

    if snap and ok_a:
        png = os.path.join(base, "A+_整段长图.jpg")
        res, err = stitch_aplus(os.path.join(base, "A+"), png)
        if res:
            w, h, n, files = res
            kb = sum(os.path.getsize(f) for f in files) // 1024
            tag = "" if len(files) == 1 else f"（分 {len(files)} 张）"
            print(f"    A+ 长图 {w}×{h}px · {n} 段 · {kb}KB{tag}")
            for k, f in enumerate(files, 1):
                rows.append({"类型": "A+长图", "序号": k, "变体": f"{n}段纵向拼接",
                             "文件": os.path.basename(f),
                             "大小KB": os.path.getsize(f) // 1024, "原始URL": ""})
        else:
            print(f"    A+ 长图跳过：{err}")

    with io.open(os.path.join(base, "图片清单.csv"), "w",
                 encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["类型", "序号", "变体", "文件", "大小KB", "原始URL"])
        w.writeheader(); w.writerows(rows)

    with io.open(os.path.join(base, "页面信息.yaml"), "w", encoding="utf-8") as f:
        f.write(f'asin: "{asin}"\n')
        f.write(f'source_url: "{url}"\n')
        f.write(f'scraped_at: "{time.strftime("%Y-%m-%d %H:%M:%S")}"\n')
        f.write("source_type: amazon_public_product_page\n")
        for k, v in facts.items():
            f.write(f'{k}: "{v}"\n')
        f.write(f"main_image_count: {ok_m}\n")
        f.write(f"aplus_image_count: {ok_a}\n")
        f.write("usage_boundary: 仅用于竞品视觉基准学习，不得直接用于自己的 Listing\n")

    flag = "" if ok_a else "   ⚠️ 未取到 A+（该商品可能没有 A+，或需渲染后才可见）"
    print(f"    主图 {ok_m} 张 · A+ {ok_a} 张{flag}")
    if facts["title"]:
        print(f"    {facts['title'][:60]}")
    return ok_m, ok_a, sess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asins", nargs="*", help="一个或多个 ASIN")
    ap.add_argument("-f", "--file", help="从文件读 ASIN，每行一个")
    ap.add_argument("-o", "--out", default="参考图_按ASIN", help="输出目录")
    ap.add_argument("-d", "--domain", default="com", help="站点后缀，如 com / co.uk / de")
    ap.add_argument("-s", "--snapshot", action="store_true",
                    help="把 A+ 模块图按顺序拼成一张长图（需 pillow，保留叙事顺序）")
    ap.add_argument("--aplus-min-kb", type=int, default=50,
                    help="A+ 图最小尺寸KB，低于此值判为图标/缩略图丢弃（默认50）")
    ap.add_argument("--force", action="store_true",
                    help="已抓过的 ASIN 也重抓（默认跳过，支持断点续抓）")
    a = ap.parse_args()

    asins = list(a.asins)
    if a.file:
        for ln in io.open(a.file, encoding="utf-8"):
            ln = ln.strip()
            if re.fullmatch(r"B0[A-Z0-9]{8}", ln):
                asins.append(ln)
    asins = [x.upper() for x in dict.fromkeys(asins)]

    if not asins:
        ap.print_help(); return 1

    os.makedirs(a.out, exist_ok=True)
    print(f"输出目录：{os.path.abspath(a.out)}")
    print(f"站点：amazon.{a.domain}　共 {len(asins)} 个 ASIN")

    # 一个会话跑到底（真实用户就是在一个会话里连看多个商品），
    # 只在被拦时才换会话换 UA。开跑前先预热拿 cookie。
    sess = new_session(a.domain)

    tm = ta = fail = 0
    cooled = False          # 全局冷却只给一次机会
    for i, asin in enumerate(asins):
        try:
            m, p, sess = run_one(asin, a.out, a.domain, sess,
                                 a.aplus_min_kb, a.snapshot, a.force)
            tm += m; ta += p
        except Blocked as e:
            if not cooled:
                cooled = True
                w = random.uniform(90, 150)
                print(f"    ✗ {e}")
                print(f"    🧊 全局冷却 {w:.0f}s，换会话后从这个 ASIN 继续 …")
                time.sleep(w)
                sess = new_session(a.domain)
                try:
                    m, p, sess = run_one(asin, a.out, a.domain, sess,
                                         a.aplus_min_kb, a.snapshot, a.force)
                    tm += m; ta += p
                    continue
                except Exception as e2:
                    print(f"    ✗ 冷却后仍失败：{e2}")
            fail += 1
            remain = asins[i:]
            print(f"\n{'='*50}")
            print("⛔ 风控持续拦截，本轮停止（硬抗只会封更久）。")
            print(f"   已完成 {i}/{len(asins)}，未完成：{' '.join(remain)}")
            print("   怎么续：")
            print("   1. 等 15–30 分钟（或把网络切到手机热点，换出口 IP）")
            print("   2. 原命令重跑 —— 抓过的自动跳过，从断的地方继续")
            print("=" * 50)
            return 1
        except Exception as e:
            fail += 1
            print(f"    ✗ {e}")
        if i < len(asins) - 1:
            w = random.uniform(8, 15)
            print(f"    等待 {w:.1f}s …")
            time.sleep(w)

    print(f"\n{'='*50}")
    print(f"完成：{len(asins)-fail}/{len(asins)} 个 ASIN　主图 {tm} 张　A+ {ta} 张")
    if fail:
        print(f"失败 {fail} 个 —— 原命令重跑即可，抓过的自动跳过")
    print("=" * 50)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
