@echo off
rem Abre o launcher (reinicia se ja estiver aberto). Precisa estar na pasta ao lado de UltraTears-builder-0.1.0-windows-x86_64\
cd /d "%~dp0"
start "" "%~dp0UltraTears-builder-0.1.0-windows-x86_64\deps\python\python.exe" LAUNCHER.py
