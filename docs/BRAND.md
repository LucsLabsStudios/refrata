# Refrata · identidade visual (LucsLabs Studios)

## Conceito

**LucsLabs** = *lux* (luz) + *labs* (laboratório). O símbolo é um **prisma de vidro em forma de play**:
um raio de luz branca entra pela esquerda e sai decomposto num espectro quente. É a ideia do produto em
uma imagem: o motor do VLC (a luz) passando pelo laboratório da LucsLabs e saindo como uma experiência
nova.

| Peça | Arquivo | Uso |
|---|---|---|
| Ícone mestre | `assets/brand/icon-1024.png` | lojas, site, materiais |
| Ícone do Windows | `assets/icon.ico` (16–256 px) | exe, instalador, atalhos, barra de tarefas |
| Ícone do app | `assets/icon.png`, `assets/brand/icon-256.png`, `icon-96.png` | janela, logo da barra lateral, cartão Sobre |
| Símbolo sem bloco | `assets/brand/symbol.png` | sobre fundos escuros, splash, vídeos |
| Key visual | `assets/brand/hero.jpg` (1536×1024), `about.jpg` (faixa) | cartão Sobre, README, posts |
| Instalador | `assets/installer/*.bmp` | lateral e cabeçalho do Inno Setup |

Até 32 px o ícone usa uma versão vetorial simplificada (contorno grosso e feixe sólido), desenhada em
`tools/brand/build_assets.py`. Os detalhes de vidro não sobrevivem a esses tamanhos.

## Cores

| Token | Hex | Papel |
|---|---|---|
| Grafite | `#0B0A09` / `#1B1B1F` | fundo do bloco do ícone e do tema escuro |
| Brasa (destaque) | `#FF7A2F` | cor de destaque padrão do app, botões, progresso |
| Âmbar | `#FFB36B` | início do espectro, eyebrow "LUCSLABS STUDIOS" |
| Magenta | `#E0398C` | fim do espectro, uso pontual |
| Luz | `#FFFFFF` | raio de entrada, texto sobre o key visual |
| Papel | `#F3EEE7` | fundo do tema claro (tons quentes) |

## Tipografia

Segoe UI Variable Display para títulos (peso Light nos grandes, no estilo "big text"), Segoe UI
Variable Text para o corpo. A palavra de destaque vem na cor brasa, como em
"Todo o motor do VLC, **refratado** numa experiência nova."

## Regras

- O prisma sempre aponta para a direita (é um play).
- Não recolorir o símbolo: o espectro é a marca. Para usos de uma cor só, use o contorno branco.
- Ao lado do símbolo, sempre "LucsLabs Studios" (assim mesmo), nunca "Lux Labs".
- "VLC" é marca da VideoLAN: em materiais públicos, deixe claro que o Refrata é independente.

## Regerar

```powershell
node tools/brand/generate.mjs --plan tools/brand/plan.json --out-dir build/brand/raw   # conceitos (gpt-image)
node tools/brand/generate.mjs --plan tools/brand/plan2.json --out-dir build/brand/raw  # símbolo + key visual
python tools/brand/build_assets.py --icon c1_prism                                       # todos os assets
```

As chaves de API vêm do `.env` do estúdio Remotion (`D:\Remotion\Lab\.env`) e nunca são copiadas
para este projeto. Os conceitos alternativos (frasco, monograma L, lente) ficam em `build/brand/raw/`.
