import zipfile
from pathlib import Path

import pytest

from app.knowledge import Knowledge, compatible


@pytest.fixture
def library(tmp_path):
    lib = Knowledge(tmp_path / 'data')
    yield lib
    lib.close()


def add(library, tmp_path, text, project='first', **kwargs):
    path = tmp_path / 'manual.md'
    path.write_text(text, encoding='utf-8')
    return library.add(path, path.name, project, background=False, **kwargs)


def test_page_citations_and_idempotent_import(library, tmp_path):
    doc = add(library, tmp_path, '# Trimming\nRipple trimming moves downstream clips.\n# Color\nLift changes shadows.')
    assert doc['status'] == 'ready'
    result = library.search('first', 'ripple trimming')['matches'][0]
    assert result['section'] == 'Trimming' and 'Ripple' in result['text']
    assert library.passage(result['id'], 'first')['hash'] == doc['hash']
    again = add(library, tmp_path, '# Trimming\nRipple trimming moves downstream clips.\n# Color\nLift changes shadows.')
    assert again['id'] == doc['id'] and len(library.list('first')) == 1
    changed = add(library, tmp_path, '# Trimming\nA changed source remains separately identifiable.')
    assert changed['id'] != doc['id']


def test_project_channel_and_version_filters(library, tmp_path):
    library.set_context('first', 'finance', '21.0.0.47')
    library.set_context('second', 'finance', '21.0')
    library.set_context('third', 'wildlife', '21.1')
    private = add(library, tmp_path, 'Secret ripple procedure.')
    shared = add(library, tmp_path, 'Channel ripple procedure.', scope='channel', application='resolve', version='21.0')
    newer = add(library, tmp_path, 'Future ripple procedure.', scope='global', application='resolve', version='21.1')
    assert {d['id'] for d in library.list('second')} == {shared['id'], newer['id']}
    result = library.search('second', 'ripple', application='resolve')
    assert [m['document'] for m in result['matches']] == [shared['id']]
    assert result['excluded_newer_versions'] == 1
    assert [m['document'] for m in library.search('third', 'ripple')['matches']] == [newer['id']]
    chunk = library.search('first', 'Secret')['matches'][0]
    with pytest.raises(ValueError):
        library.passage(chunk['id'], 'second')
    with pytest.raises(ValueError):
        library.source(private['id'], 'second')


def test_cancellation_and_restart_never_expose_partial_index(library, tmp_path, monkeypatch):
    import app.knowledge as module
    original = module.pages

    def interrupted(path):
        yield 1, 'Section', 'Cancellation needle on first page', 2, ''
        library.cancelled.add(next(iter(library.list('first')))['id'])
        yield 2, 'Section', 'Second page', 2, ''
    monkeypatch.setattr(module, 'pages', interrupted)
    doc = add(library, tmp_path, 'temporary')
    assert doc['status'] == 'cancelled'
    assert library.search('first', 'needle')['matches'] == []
    monkeypatch.setattr(module, 'pages', original)
    with library.connect() as db:
        db.execute("UPDATE documents SET status='indexing' WHERE id=?", (doc['id'],))
    restarted = Knowledge(library.root.parent)
    assert restarted.get(doc['id'], 'first')['status'] == 'interrupted'
    assert restarted.search('first', 'needle')['matches'] == []
    restarted.close()


def test_pdf_pages_and_scanned_page_warning(library, tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 50 700 Td (Ripple trimming changes the timeline.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_blank_page(612, 792)
    writer.add_outline_item('Timeline edits', 0)
    path = tmp_path / 'guide.pdf'
    writer.write(path)
    doc = library.add(path, path.name, 'first', background=False)
    assert doc['status'] == 'ready' and doc['total'] == 2
    assert doc['warnings'][0]['page'] == 2
    result = library.search('first', 'trimming')['matches'][0]
    assert result['page'] == 1 and result['section'] == 'Timeline edits'
    assert 'PDF page 1' in result['citation']


def test_encrypted_malformed_and_empty_documents_fail_honestly(library, tmp_path):
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.encrypt('secret')
    path = tmp_path / 'locked.pdf'
    writer.write(path)
    doc = library.add(path, path.name, 'first', background=False)
    assert doc['status'] == 'failed' and 'Encrypted' in doc['error']
    path.write_bytes(b'not a PDF')
    doc = library.add(path, path.name, 'first', background=False)
    assert doc['status'] == 'failed'
    doc = add(library, tmp_path, '')
    assert doc['status'] == 'failed' and 'No readable text' in doc['error']


def test_docx_sections_and_safe_search(library, tmp_path):
    path = tmp_path / 'notes.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Audio</w:t></w:r></w:p><w:p><w:r><w:t>Ducking lowers music under narration.</w:t></w:r></w:p></w:body></w:document>')
    doc = library.add(path, path.name, 'first', background=False)
    assert doc['status'] == 'ready'
    match = library.search('first', 'ducking " OR *')['matches'][0]
    assert match['section'] == 'Audio' and match['page'] == 2
    assert library.search('first', 'unfindableword')['matches'] == []


def test_remove_forgets_index_and_preserves_original(library, tmp_path):
    doc = add(library, tmp_path, 'Unique forgotten phrase')
    library.remove(doc['id'], 'first')
    assert library.list('first') == [] and library.search('first', 'forgotten')['matches'] == []
    assert len(list((library.root / 'trash').glob('*.md'))) == 1


def test_version_comparison_and_channel_requires_assignment(library, tmp_path):
    assert compatible('21.0', '21.0.0.47') and not compatible('21.1', '21.0.0.47')
    assert compatible('', '21.0') and not compatible('experimental', 'stable')
    with pytest.raises(ValueError, match='channel'):
        add(library, tmp_path, 'Shared', scope='channel')
