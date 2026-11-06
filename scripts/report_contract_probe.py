"""Native S1-S5 renderer/checker compatibility; never a business approval."""
import importlib.util
from pathlib import Path
import sys
import tempfile


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(home):
    home = Path(home)
    results = []
    errors = []
    previous = sys.path[:]
    sys.path.insert(0, str(home/'scripts'))
    try:
        renderer = _load(home/'skills/github-tabler-html-report/assets/salon_report.py', '_native_theme_renderer')
        checker = _load(home/'scripts/检查HTML达标.py', '_native_theme_checker')
        with tempfile.TemporaryDirectory(prefix='starsky-theme-') as folder:
            from PIL import Image
            folder = Path(folder)
            image = folder/'format-fixture.png'
            Image.new('RGB', (4, 4), 'white').save(image)
            for stage in ('S1', 'S2', 'S3', 'S4', 'S5'):
                report = renderer.Report(stage+' 格式兼容性测试，非业务验收', stage=stage)
                if stage == 'S1':
                    report.product_decision(product_name='格式夹具', product_id='THEME-ONLY',
                        image_path=image, image_source='生成的格式夹具', image_role='selected',
                        verdict='watch', scope='demo', conclusion='不代表真实商品或业务通过',
                        next_action='按实际产品执行业务验收', evidence_date='2026-10-16')
                section = report.section('format', '渲染与检查兼容')
                section.raw('<div class="kpirow"><span>非业务验收</span></div>')
                section.table(['范围', '说明'], [['格式', '不改变业务门槛']])
                target = folder/(stage+'.html')
                target.write_text(report.render(), encoding='utf8')
                measured = checker.measure(target)
                passed = measured['charset_ok'] and measured['style_ok']
                results.append({'stage': stage, 'ok': passed})
                if not passed: errors.append(stage+': '+str(measured))
    except (OSError, ValueError, ImportError, KeyError, TypeError) as error:
        errors.append('renderer/checker: '+str(error))
    finally:
        sys.path[:] = previous
    return {'ok': not errors and len(results) == 5, 'errors': errors, 'stages': results,
            'scope': 'format compatibility only; business gates remain independent'}
