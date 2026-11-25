"""Offline script adapter to the same audited JS engine used by NWF.
Node >=20 must be installed. No network/provider or model fallback.
"""
from __future__ import annotations
import json,subprocess,sys
from importlib.resources import files
from pathlib import Path

def execute(request:dict)->dict:
    runner=files(__package__).joinpath('runtime','cli.mjs')
    try:
        p=subprocess.run(['node',str(runner)],input=json.dumps(request,allow_nan=False),
                         text=True,capture_output=True,timeout=30,check=False)
    except FileNotFoundError as e:raise RuntimeError('Node >=20 required for the shared offline recipe engine') from e
    except subprocess.TimeoutExpired as e:raise RuntimeError('Recipe engine timed out after 30s') from e
    if p.returncode:raise RuntimeError('Recipe engine failed: '+p.stderr.strip())
    return json.loads(p.stdout)

def main():
    import argparse
    a=argparse.ArgumentParser(description='Offline SE1,000-recipe runner; JSON stdin/stdout, no data fetch')
    a.add_argument('--list',action='store_true');a.add_argument('--scope',choices=['stock','market'])
    a.add_argument('--query',default='');a.add_argument('--recipe');a.add_argument('--method');a.add_argument('--list-methods',action='store_true');a.add_argument('--input',type=Path)
    p=a.parse_args()
    if p.list_methods:req={'action':'list-methods'}
    elif p.list:req={'action':'list','filter':{'scope':p.scope,'query':p.query}}
    else:
        if p.recipe and p.method:a.error('choose exactly one of --recipe or --method')
        if not (p.recipe or p.method):a.error('--recipe or --method required unless listing')
        req={('method_id' if p.method else 'recipe_id'):(p.method or p.recipe),'input':json.loads(p.input.read_text() if p.input else sys.stdin.read())}
    try:print(json.dumps(execute(req),allow_nan=False))
    except (ValueError,RuntimeError,subprocess.TimeoutExpired) as e:print(str(e),file=sys.stderr);raise SystemExit(2)
if __name__=='__main__':main()
