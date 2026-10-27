"""Actual browser, WebSocket, Windows ConPTY and official stdio MCP client. No real account."""
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.name!='nt',reason='Windows native terminal browser integration')
@pytest.mark.parametrize('program,empty_accounts',[('claude',False),('claude',True),('codex',False)])
def test_claude_code_inside_one_production_in_chrome(tmp_path,program,empty_accounts):
    proc=subprocess.Popen([sys.executable,str(Path(__file__).parent/'fakes'/'native_terminal_app.py'),str(tmp_path/'data'),
                           '--program',program,*(['--empty-accounts'] if empty_accounts else [])],
                          cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    try:
        first=proc.stdout.readline()
        match=re.search(r'launch: (http://127\.0\.0\.1:\d+/#t=(\S+))',first)
        assert match, first
        logs=[]
        threading.Thread(target=lambda:logs.extend(proc.stdout),daemon=True).start()
        result=subprocess.run([sys.executable,str(ROOT/'tools'/'claude_terminal_ui_check.py'),match.group(1),str(tmp_path/'shots')],
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=110,
                              env={**os.environ,'UI_CLAUDE_EMPTY_ACCOUNTS':str(int(empty_accounts)),'UI_CLI_PROGRAM':program})
        if result.returncode==3:
            pytest.skip('Chrome is not installed')
        assert result.returncode==0, (result.stdout+result.stderr+''.join(logs)[-2500:]).replace(match.group(2),'<token>')
        assert 'FAIL' not in result.stdout
    finally:
        proc.terminate(); proc.wait(10)
