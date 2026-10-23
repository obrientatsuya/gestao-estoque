from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import unittest


class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.current=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':self.active='src' not in dict(attrs);self.current=[]
    def handle_data(self,data):
        if self.active:self.current.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.active:self.scripts.append(''.join(self.current));self.active=False


class TestHtmlScripts(unittest.TestCase):
    def test_all_inline_javascript_parses(self):
        root=Path(__file__).resolve().parents[1]
        parser=Scripts();parser.feed((root/'HL_COORDENADAS_v7.html').read_text(encoding='utf-8'))
        process=subprocess.run(['node','-e','const vm=require("node:vm");const fs=require("node:fs");for(const text of JSON.parse(fs.readFileSync(0,"utf8")))new vm.Script(text);'],input=json.dumps(parser.scripts),text=True,capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr)
