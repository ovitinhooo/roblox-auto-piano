# 🎹 Auto Piano — Roblox

App para Windows que toca músicas automaticamente nos pianos do Roblox, simulando o teclado no layout "Virtual Piano" (61 teclas, `t` = Dó central).

## Recursos

- Toca **arquivos MIDI** (`.mid`) — escolhe a oitava automaticamente e ignora a bateria
- Toca **partituras em texto** no formato Virtual Piano
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
