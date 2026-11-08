"""The portable review uses local artifacts and treats metadata as text."""
import json
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

from forge.gif_clip import import_gif


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.images, self.payload, self.in_payload, self.text = [], [], '', False, []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.links.append(attrs.get('href'))
        if tag == 'img':
            self.images.append(attrs.get('src'))
        if tag == 'script' and attrs.get('id') == 'review-data':
            self.in_payload = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_payload = False

    def handle_data(self, data):
        if self.in_payload:
            self.payload += data
        else:
            self.text.append(data)


def make_review(tmp_path, **kwargs):
    source = tmp_path / 'input.gif'
    a, b = Image.new('RGB', (12, 10), 'red'), Image.new('RGB', (12, 10), 'blue')
    a.save(source, save_all=True, append_images=[b], duration=[80, 230], loop=2)
    out = tmp_path / 'out'
    report = import_gif(source, out, **kwargs)
    return out, report


def test_review_page_links_existing_relative_artifacts_and_preserves_timing_data(tmp_path):
    out, report = make_review(tmp_path)
    assert report['review']['page'] == 'review.html'
    page = Page((out / 'review.html').read_text())
    assert {'source.gif', 'animation.gif', 'sheet.png', 'manifest.json', 'gif-report.json'} <= set(page.links)
    for link in page.links + page.images:
        assert link and not Path(link).is_absolute() and ':' not in link
        assert (out / link).is_file(), link
    data = json.loads(page.payload)
    assert data['durations_ms'] == [80, 230]
    assert data['gif_loop_count'] == 2
    assert data['frames'] == ['frames/f0.png', 'frames/f1.png']
    assert data['size'] == [12, 10]
    content = ' '.join(page.text)
    assert 'opaque_frame' in content
    assert 'endpoint_mismatch' in content
    assert 'awaiting_visual_review' in content


def test_review_treats_untrusted_tag_as_text_not_markup(tmp_path):
    tag = '</script><img src=x onerror=alert(1)>&"'
    out, _ = make_review(tmp_path, tag=tag)
    page = Page((out / 'review.html').read_text())
    assert 'x' not in page.images
    assert tag in ' '.join(page.text)
    json.loads(page.payload)


def test_review_explains_removal_without_claiming_art_approval(tmp_path):
    out, report = make_review(tmp_path, background_color='#ff0000')
    content = ' '.join(Page((out / 'review.html').read_text()).text)
    assert '#ff0000' in content
    assert 'background_emptied_frame' in content
    assert 'outline' in content
    assert report['review']['status'] == 'awaiting_visual_review'


def test_generated_review_controls_keep_timing_and_stop_duplicate_timers(tmp_path):
    import shutil
    import subprocess
    import pytest
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is only needed for the optional review-player script test')
    out, _ = make_review(tmp_path)
    result = subprocess.run([node, str(Path(__file__).with_name('gif_review_controls.cjs')),
                             str(out / 'review.html')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
