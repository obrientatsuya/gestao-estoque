"""Sincroniza os mesmos assets nos dois servidores, sem depender de CDN."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    shutil.copytree(ROOT/'ui', ROOT/'HL_Conferencia_NFe'/'static'/'ui', dirs_exist_ok=True)
    print('Assets offline sincronizados.')


if __name__ == '__main__':
    main()
