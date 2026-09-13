import ast
from pathlib import Path
from lxml import etree

ROOT=Path(__file__).resolve().parents[1]/'l10n_ec_sri_community'

def test_manifest_files_and_python_syntax():
    manifest=ast.literal_eval((ROOT/'__manifest__.py').read_text(encoding='utf-8'))
    assert manifest['version'].startswith('19.0.')
    assert all('edi' not in dep for dep in manifest['depends'])
    for name in manifest['data']: assert (ROOT/name).is_file(),name
    for file in ROOT.rglob('*.py'): ast.parse(file.read_text(encoding='utf-8'),filename=str(file))

def test_odoo_xml_is_well_formed_and_local_actions_exist():
    ids=set();actions=[]
    for folder in ('views','data','security','wizard','report'):
        for file in (ROOT/folder).glob('*.xml'):
            tree=etree.parse(str(file))
            assert tree.getroot().tag=='odoo'
            for node in tree.xpath('//*[@id]'):
                assert node.get('id') not in ids,node.get('id')
                ids.add(node.get('id'))
            actions+=tree.xpath('//menuitem/@action')
    assert all(action in ids for action in actions)

def test_every_object_button_method_exists():
    methods=set()
    for file in ROOT.rglob('*.py'):
        methods.update(node.name for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))) if isinstance(node,ast.FunctionDef))
    for file in ROOT.rglob('*.xml'):
        for button in etree.parse(str(file)).xpath('//button[@type="object"]'):
            assert button.get('name') in methods,(file,button.get('name'))
