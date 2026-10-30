@echo off
rem Reaplica os patches locais do UltraTears (60 FPS / tempo real / AMD RX 9060 / cutscenes) e recompila.
rem Use depois de atualizar o builder (unzip por cima), que sobrescreve esses arquivos.
setlocal
set "B=%~dp0UltraTears-builder-0.1.0-windows-x86_64"
set "P=%~dp0patches-locais"
if not exist "%B%\setup.bat" (echo Pasta do builder nao encontrada: %B% & pause & exit /b 1)
echo Copiando patches de %P% para o builder...
xcopy /y /i "%P%\tools\lifter\patches.py" "%B%\tools\lifter\" >nul
xcopy /y /i "%P%\tools\build_game.py" "%B%\tools\" >nul
xcopy /y /i "%P%\src\host\frame_step.c" "%B%\src\host\" >nul
xcopy /y /i "%P%\src\host\fs.c" "%B%\src\host\" >nul
xcopy /y /i "%P%\src\host\svc.c" "%B%\src\host\" >nul
xcopy /y /i "%P%\tools\make_heap_overlay.py" "%B%\tools\" >nul
xcopy /y /i "%P%\tools\make_mod_rstb.py" "%B%\tools\" >nul
xcopy /y /i "%P%\tools\lifter\gen.py" "%B%\tools\lifter\" >nul
xcopy /y /i "%P%\src\host\trainer.c" "%B%\src\host\" >nul
xcopy /y /i "%P%\src\host\window_sdl.c" "%B%\src\host\" >nul
xcopy /y /i "%P%\src\host\overrides_input.c" "%B%\src\host\" >nul
xcopy /y /i "%P%\src\vk\present.c" "%B%\src\vk\" >nul
xcopy /y /i "%P%\src\vk\vk.h" "%B%\src\vk\" >nul
xcopy /y /i "%P%\src\vk\vk.c" "%B%\src\vk\" >nul
xcopy /y /i "%P%\src\gfx\vkb_draw.c" "%B%\src\gfx\" >nul
xcopy /y /i "%P%\src\gfx\backend_vk.c" "%B%\src\gfx\" >nul
xcopy /y /i "%P%\src\gfx\vkb.h" "%B%\src\gfx\" >nul
xcopy /y /i "%P%\src\gfx\vkb_shader.c" "%B%\src\gfx\" >nul
xcopy /y /i "%P%\src\gfx\vkb_shader.h" "%B%\src\gfx\" >nul
xcopy /y /i "%P%\src\runtime\platform\windows.c" "%B%\src\runtime\platform\" >nul
cd /d "%B%"
echo Traduzindo o codigo do jogo de novo (lift, ~2 min)...
"deps\python\python.exe" tools\setup.py --only lift
if errorlevel 1 (echo ERRO no lift & pause & exit /b 1)
echo Compilando (so o que mudou)...
"deps\python\python.exe" tools\setup.py --only compile
if errorlevel 1 (echo ERRO na compilacao & pause & exit /b 1)
echo.
echo Pronto: UltraTears.exe atualizado com os patches.
pause

