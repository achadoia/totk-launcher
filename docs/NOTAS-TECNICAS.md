# UltraTears: 60 FPS / FPS dinâmico trazidos do TOTK Optimizer (UltraCam / DFPS)

## O que foi analisado

`D:\zelda\optimizer` é o **NX Optimizer 3.2.0** (MaxLastBreath). Ele não melhora o jogo por si: é um gerenciador
que instala o mod **UltraCam** (um módulo `subsdk3` em ARM64, fechado, carregado via `main.npdm`) e um `.ini`
(`romfs/UltraCam/TOTK/Config/maxlastbreath.ini`) em um emulador (Yuzu/Ryujinx) ou num Switch desbloqueado.
Também ajusta configurações do emulador (RAM estendida, escala) e tem uma lista de cheats por versão.

Nada disso roda direto no UltraTears, por três motivos:

- O UltraTears **recompila estaticamente** o código do jogo para C/x86-64. Um `subsdk3` ARM64 que faz hooks em
  tempo de execução (exlaunch) não tem como ser carregado.
- O UltraCam suporta TOTK até a **1.4.2**; o nosso dump é a **1.4.3** e os endereços mudaram (verifiquei: os cheats
  da 1.4.2 não batem com o binário 1.4.3).
- Os presets de emulador (Yuzu/Ryujinx ini) não têm equivalente aqui.

O que **vale** do Optimizer é a lista de funcionalidades: FPS dinâmico (jogo na velocidade certa em qualquer fps),
destravar 60 fps, resolução interna, sombras, FOV, câmera, etc.

## O que foi portado para o PC

O projeto-mãe do UltraTears (MacNativeGames, código aberto, MIT) já tinha esses patches **para a mesma versão 1.4.3**
em `games/totk/runtime/game_patches_v143.c`, mas como patches de bytes ARM64 aplicados na memória, que o port
Windows não tinha portado (o próprio `hle_nvn.c` dizia: "Uncapped needs MacNativeGames' 60 FPS game patches and
real-time frame step (game_patches_v143.c), not ported yet"). Traduzi cada site para C no lifter:

| Recurso (nome no Optimizer) | O que faz aqui | Variável (padrão) |
|---|---|---|
| **FPS 60** | 7 instruções do controlador de intervalo (`main+0x2db174`): mínimo 1 em vez de 2 → 60 fps quando o frame cabe em 16,7 ms | `TOTK_FPS60` (on) |
| **Dynamic FPS** | 7 sites: o jogo passa a avançar o tempo pelo tempo real medido entre presents (média de 5 frames), não pelo intervalo escolhido. 45 fps viram 45 fps em velocidade normal, sem slow motion | `TOTK_REALTIME` (on) |
| (parte do DFPS) | relógio de roupa/cabelo em 4 sites de atores, para não acelerar em fps alto | `TOTK_CLOTH_REALTIME` (on) |
| (parte do DFPS) | trampolins acima de ~100 fps: a checagem de "tempo lento" passa a usar a escala de tempo | `TOTK_HIGHFPS_FIX` (on) |
| **Resolution** (experimental) | tamanho da cena renderizada (`main+0x585e48`), em vez dos 1600x900 do modo docked | `TOTK_RENDER_RES=1920x1080` (off) |
| Dynamic Resolution | já existia no UltraTears (`TOTK_DRS`, off) | |
| Handheld/Docked | já existia (`TOTK_OPERATION_MODE`) | |

Arquivos mudados no builder (cópia em `D:\zelda\patches-locais\`):

- `tools/lifter/patches.py`: os patches (cada um confere a instrução original antes; se o builder mudar de
  versão, o lift falha com mensagem clara em vez de alterar código errado).
- `src/host/frame_step.c` (novo): mede o tempo entre presents e publica `{dt*30, dt, dt*60}`; lê as variáveis.
- `tools/build_game.py`: compila o arquivo novo.

Tudo é compilado dentro do jogo e ligado/desligado por variável de ambiente na hora de rodar, então um único
`UltraTears.exe` serve para os dois comportamentos.

## Como usar

- `D:\zelda\JOGAR-60FPS.bat`: padrão (60 fps + tempo real). Log em `D:\zelda\ultratears-run.log`.
- `D:\zelda\JOGAR-30FPS-ORIGINAL.bat`: comportamento original, para comparar.
- `D:\zelda\JOGAR-60FPS-1080p.bat`: experimental, cena em 1920x1080. As heaps de textura do jogo continuam do
  tamanho original (o Mac cresce as heaps com um overlay no RomFS, não portado): se fechar sozinho, volte ao padrão.
- `D:\zelda\REAPLICAR-PATCHES.bat`: depois de atualizar o builder, copia os patches de volta e recompila.

Também dá para fixar no `ultratears.cfg` (o jogo lê linhas `TOTK_*=valor` dele).

**`TOTK_MAX_INTERVAL=1`** (já no `JOGAR-60FPS.bat`): o controlador do jogo só conhece intervalo 1 (60) ou 2 (30) e
cai para 2 assim que o trabalho de CPU passa de 16,7 ms. Com o máximo fixo em 1, o jogo apresenta todo frame e roda
nos fps que a CPU der (40-55 nesta máquina), e o passo de tempo real mantém a velocidade correta. É isso que o
UltraCam chama de FPS dinâmico. Sem essa variável o jogo fica pulando entre 60 e 30.

No log, as linhas `[totk] 1.4.3 60 FPS: on ...` e `[totk] 1.4.3 real-time frame step: on ...` confirmam que os
patches estão ativos; `[nvn] 游戏设出图间隔 2 → 1` mostra o controlador indo para 60.

## 1440p (cena renderizada em 2560x1440)

Três peças, todas feitas em 2026-10-27:

- `tools/make_heap_overlay.py`: lê `System/HeapSize/Product.Nin_NX_NVN.HeapDef.byml.zs` direto da imagem, sobe
  `DynamicTextureAllocatorMem` (113664 → 398336 KiB) e `Graphics` (201728 → 486400 KiB) pela fórmula do Mac
  (+278 MiB para 2560x1440) e grava o arquivo recomprimido em `data\romfs-overlay\` + `overlay.json`.
  `GERAR-OVERLAY-1440p.bat` roda isso (já rodado).
- `src/host/fs.c`: pasta `data\romfs-overlay` (ou `TOTK_ROMFS_OVERLAY`) tem prioridade sobre a imagem, por arquivo.
- `src/host/svc.c` + `frame_step.c`: o pool de memória do jogo (3,2 GiB) cresce pelo mesmo extra; se o overlay faltar
  ou for menor que o `TOTK_RENDER_RES` pedido, o log avisa em vermelho.

`JOGAR-60FPS-1440p.bat` liga tudo. No log: `[totk] RomFS overlay: System/HeapSize/...`, `[totk] texture heaps grown by
278 MiB (overlay in place)`, `[totk] application pool 3550 MiB`, `render resolution: scene 2560x1440`.

## Trainer (cheats)

Os cheats do Optimizer são da lista MyNXCheats (Arch9SK7); existe uma versão exata para o build 1.4.3
(`277178B7DBA1B6D4`), com 72 entradas e um "Restore Code" com a instrução original de cada endereço. Entraram:

- `tools/lifter/patches.py`: ~45 sites viraram `switch (trainer_sel(id))` no C gerado: a instrução do cheat quando o
  cheat está ligado, a original quando não (cada original conferida no lift). Os "code caves" da lista (vida,
  durabilidade, buff de comida, +60 s) foram reescritos em C.
- `src/host/trainer.c`: estado, atalhos, "segurar A/B/X" lendo o controle, planador (float em rodata) e Moon Jump
  (cadeia de ponteiros) escritos por frame, texto na barra de título. `TOTK_CHEATS=` liga na abertura.
- `gen.py`: patches podem desviar (`b`/`b.cond`/`ret`/`bl`) e ganham rótulos.

Teclas: `D:\zelda\TRAINER-TECLAS.txt`. Fora: as 8 entradas de resolução handheld/docked da lista (temos
`TOTK_RENDER_RES`).

## Launcher (`D:\zelda\LAUNCHER.bat`): mods, trainer e modo, estilo WeMod

`LAUNCHER.py` (Python do builder, sem depender da política de scripts do Windows) serve a página `launcher\ui.html`
em `http://127.0.0.1:<porta>` (só local) e a abre numa janela do Edge em modo app (perfil próprio em
`launcher\edge-profile`, sem barra de endereço); fechou a janela, o launcher fecha. Sem Edge, abre no navegador padrão.
Três abas:

- **Mods**: um toggle por pasta `romfs` encontrada (arquivos de `D:\zelda\mods` extraídos para `mods\_extraidos`, pastas
  já extraídas mesmo sem o arquivo, ou pastas soltas em `mods\`), em grupos Prontos / Com aviso / Incompatíveis, busca,
  avisos (exefs ignorado, `.tkcl` só via TKMM, RSTB ou pasta "Game Version" de outra versão, `.bnp` do BOTW) e a lista de
  arquivos em conflito entre os ligados (o mod mais abaixo vence). Mods só entram quando o jogo abre pelo launcher.
- **Trainer**: os 28 cheats do trainer embutido com toggle ou seletor de nível e a tecla do jogo ao lado. Com o jogo
  fechado, o que está ligado entra na abertura (`TOTK_CHEATS=...`). Com o jogo aberto, ligar e desligar vale na hora e
  as teclas apertadas no jogo refletem no launcher: a troca é pela memória compartilhada `Local\UltraTearsTrainer`
  (4096 bytes; `TrainerShm` em `src/host/trainer.c`: o jogo conta frames em `game_tick` e espelha os seletores em `cur[]`,
  o launcher escreve `req[]` e incrementa `gui_seq`, o jogo aplica e responde `game_seq`). A barra mostra o fps.
- **Jogo**: o modo (30 fps / 60 / 60+1080p / 60+1440p), log do jogo, teclas, fechar o jogo.

Escolhas em `mods\mods.json` (modo, mods ligados, cheats e a lista `seen` dos mods já vistos); log do launcher em
`launcher\launcher.log`. `LAUNCHER.bat --console` abre a versão antiga em texto (`LAUNCHER-console.py`).

- **Tudo fica salvo**: cada toggle de mod, de cheat e o modo vão para `mods.json` na hora, e também o que você liga
  dentro do jogo (tecla ou menu F1) assim que o launcher o vê pela memória compartilhada. Na próxima vez os cheats
  salvos entram na abertura e os mods continuam como estavam.
- **Mod novo entra ligado**: uma pasta ou arquivo novo em `mods\` aparece já ligado na próxima abertura do launcher
  (aviso "Mod novo ligado"), menos pastas "Game Version 1.x" de outra versão, que ficam desligadas. Mods que não têm
  como entrar (só `.tkcl`, `.bnp`) ficam no grupo Incompatíveis.
- **Testes de abertura feitos aqui (27/10)**, anotados em `KNOWN` no `LAUNCHER.py` e mostrados no aviso do mod: o
  Dragon Glider trava a 1.4.3 na abertura (Player.pack e Parasail.pack da 1.2.1), com ou sem o Second Quest; o
  Warglaives só abre com o Second Quest ligado (sem ele trava ao carregar o ResidentCommon.pack dele); o 4k_v12s é um
  mod de emulador (só o HeapDef entraria). Esses três ficam desligados por padrão. Os outros 22 mods abrem juntos até a
  tela de título a 60 fps, com o pack 4K; dentro do mundo só o jogo diz.
- **Abrir o `LAUNCHER.bat` de novo** fecha o launcher anterior (`launcher\pid.txt`), para que a janela sempre rode o
  `LAUNCHER.py` atual.

Nada é copiado: o jogo recebe `TOTK_ROMFS_OVERLAY=pasta1;pasta2;...` e lê as pastas dos mods ligados por cima da
imagem (a primeira vence; `data\romfs-overlay`, com o HeapDef, vem sempre primeiro; `fs.c` aceita até 64 pastas,
eram 16 e com mais mods as últimas eram ignoradas em silêncio). Mods de textura (`TexToGo/*.txtg`,
como o pacote HD de 6,9 GB) têm as texturas convertidas para BC7 no cache uma única vez antes de abrir o jogo
(`tools\bc7_build.py` na pasta do mod; marcador `.bc7-cache-done`), porque o cache é indexado por conteúdo e as
originais continuam lá.

`fs.c` também serve arquivos que o RomFS não tem (mods que adicionam atores): no início o índice é fundido com as
pastas de overlay.

**RSTB e HeapDef dos mods (`tools\make_mod_rstb.py`, chamado pelo launcher ao abrir o jogo).** O jogo aloca cada
recurso com o tamanho que a tabela `System/Resource/ResourceSizeTable.Product.143.Nin_NX_NVN.rsizetable.zs` lista; um
arquivo de mod maior que a entrada falha ao carregar ("NoFile" no log) e o jogo cai logo depois. O pack de texturas 4K
(`HDPacks.com_Totk`, 10.407 texturas, 10.382 maiores que a tabela) só traz tabelas até a 1.2.1, então o launcher gera a
da 1.4.3 em `mods\_gerado\romfs` (entrada de `.txtg` = tamanho arredondado a 32 + 288, medido na tabela original; os
outros formatos por proporção; refeita só quando os mods ligados mudam, `signature.json`). Os HeapDefs de todos os
mods ligados são fundidos com o nosso (o maior de cada heap: o pack sobe `TextureHeap` de 495 MiB para 3,8 GiB, o
Second Quest sobe `RSDB` e `UI`; se só o primeiro valesse, o pack ficaria sem heap com os dois ligados) e `overlay.json` leva o
crescimento para o pool do jogo (`svc.c`: cap do heap subiu de 4 para 8 GiB; `frame_step.c` lê o `overlay.json` da
primeira pasta de `TOTK_ROMFS_OVERLAY` que o tiver). Também em `vkb_draw.c`: a memória de upload de cada slot de GPU
agora é liberada acima de 256 MiB ao fim da submissão (antes cada um dos 8 slots guardava o pico para sempre e, com o
pack, o heap visível ao host de 8 GiB acabava: "upload memory failed (-2)" aos 43 s). Com isso o pack 4K abre e roda a
60 fps em 1440p. O launcher ainda não funde `.pack.zs` de dois mods; para mods de atores da era 1.2.1, o TKMM (suporta
1.4.x) gera uma pasta `romfs` já fundida com a RSTB certa: coloque-a zipada em `mods\` e o launcher usa a RSTB dela.

## O que esperar nesta máquina

No último log (antes dos patches), o trabalho de CPU por frame do jogo ficou em **p50 18,4 ms / p90 23,6 ms**
(Ryzen 5 2600X). Ou seja: o jogo vai ficar na faixa de **40-55 fps** na maior parte do tempo, com o controlador
alternando entre intervalo 1 e 2; graças ao tempo real a velocidade do jogo fica correta nos dois casos. A GPU
(RX 9060 XT) sobra, por isso a cena em 1080p é "de graça" para a GPU, mas limitada pelas heaps.

## O que não foi portado (e por quê)

- **Sombras, FOV, distância de render, velocidade da câmera, freecam, menu F12, presets de cor**: são do UltraCam
  (fechado, 1.4.2). Precisariam de engenharia reversa própria no 1.4.3. Sombras/FOV/distância são valores em memória
  e seriam os próximos candidatos.
- **Cheats**: os endereços do Optimizer são da 1.4.2; nenhum confere no 1.4.3.
- **Resolução de saída (1440p/4K) e ultrawide**: o Mac tem (`patch_output_res`, `aspect_install`), mas dependem do
  painel de configurações do Mac e do overlay de heaps; ficam para depois.
- **Uncapped (>60 fps)**: o `hle_nvn.c` tem `TOTK_UNCAPPED=1` que agora faz sentido (o passo de tempo real existe),
  mas não testei; num monitor de 60 Hz não muda nada.
