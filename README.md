# 🎹 Auto Piano — Roblox

App para Windows que toca músicas automaticamente nos pianos do Roblox, simulando o teclado no layout "Virtual Piano" (61 teclas, `t` = Dó central).

## Recursos

- Toca **arquivos MIDI** (`.mid`) — escolhe a oitava automaticamente
- **Escolha de instrumentos do MIDI**: ligue/desligue cada parte, ou use "Só melodia"
- **Prévia no PC** (🔊): ouve a música pelo som do Windows antes de ir pro jogo
- **Só toca com o Roblox em foco**: se você trocar de janela, pausa e solta as teclas; volta sozinho quando o Roblox volta ao foco
- **Lembra tudo**: opções, última música e as configurações de cada música (velocidade, BPM, tom, instrumentos) ficam em `config.json`
- Toca **partituras em texto** no formato Virtual Piano
- **Notas longas**: segura cada tecla pelo tempo certo da nota (ou modo de notas curtas)
- **Sustain** com Espaço: alternar (liga no início e desliga no fim) ou segurar
- **Modo 88 teclas**: notas extras graves e agudas com **Ctrl**, para pianos que suportam
- **Procurar nas minhas músicas**: filtra a lista enquanto você digita, sem ligar para acentos ou maiúsculas (Ctrl+F, Enter seleciona, Esc limpa)
- Busca de partituras/MIDI no navegador e botão para colar a partitura copiada
- Controle de velocidade, BPM, transposição e contagem regressiva
- Atalhos globais: **F5** tocar/pausar, **F6** parar
- Sem dependências: só Python 3

## Como usar

1. Instale o [Python 3](https://www.python.org/downloads/)
2. Baixe este repositório e dê dois cliques em `Abrir Piano.bat`
3. Escolha uma música, clique em **▶ Tocar** e clique na janela do Roblox (ou aperte **F5** com o Roblox aberto)

Para adicionar músicas, use **Importar…** (MIDI ou `.txt`) ou **Nova** para colar uma partitura. As músicas ficam na pasta `musicas/`.

## Formato da partitura

| Símbolo | Significado |
|---|---|
| `t`, `y`, `T`… | uma nota |
| `[tuo]` | acorde (notas juntas) |
| `[t u o]` | arpejo rápido |
| espaço, quebra de linha, `-` | pausa de 1 passo |
| `\|` | pausa de 2 passos |
| `# bpm: 200` | define o tempo |

> Dica: teste primeiro no Bloco de Notas — se aparecerem letras, está funcionando. Se o Roblox não reagir, abra o app como administrador.
