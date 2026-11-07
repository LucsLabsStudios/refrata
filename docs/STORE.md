# Publicar o Refrata na Microsoft Store

Pela Store, quem assina o pacote é a própria Microsoft. Quem instala por lá não vê o alerta do
SmartScreen, e você não precisa comprar um certificado.

## 1. Conta e nome (uma vez)

1. Crie a conta de desenvolvedor em <https://storedeveloper.microsoft.com>. Conta individual é
   gratuita. Conta de empresa (em nome da LucsLabs Studios) passa por verificação da empresa.
2. No Partner Center, em **Apps e jogos › Novo produto › Aplicativo MSIX ou PWA**, reserve o nome
   **Refrata**. Se o nome estiver livre, a reserva já garante que ninguém mais publica com ele na Store.
3. Abra **Gerenciamento de produtos › Identidade do produto** e copie os três valores para
   `installer/store.json`, seguindo o modelo de `installer/store.example.json`:

   | Partner Center | store.json |
   |---|---|
   | Package/Identity/Name | `name` |
   | Package/Identity/Publisher (`CN=…`) | `publisher` |
   | Package/Properties/PublisherDisplayName | `publisherDisplayName` |

## 2. Gerar o pacote

```powershell
python tools/build.py           # gera também dist\Refrata-1.0.0.0-x64-store.msix
```

O pacote da Store sai **sem assinatura**, porque a Microsoft assina na publicação. A cada nova versão,
aumente `__version__` em `refrata/__init__.py`. A Store exige que o último número da versão seja 0, e
o build já cuida disso.

## 3. Envio

| Seção | O que preencher |
|---|---|
| Preços e disponibilidade | Gratuito; mercados à sua escolha |
| Propriedades | Categoria **Multimídia** (ou Música e vídeo); marque "acessa a internet" (streams) |
| Classificação etária | Questionário rápido: o app não tem conteúdo próprio |
| Pacotes | Envie `Refrata-1.0.0.0-x64-store.msix` |
| Listagem (pt-BR) | Descrição, no mínimo uma captura de 1366×768 ou maior, palavras-chave |
| Capacidade restrita `runFullTrust` | Justificativa: "Aplicativo de desktop Win32 (PySide6/Qt) empacotado em MSIX; precisa de confiança total para reproduzir mídia com o libVLC." |

A certificação costuma levar de algumas horas a três dias úteis.

## 4. Pontos de atenção na revisão

- **Marca VLC:** na listagem, descreva como "baseado no libVLC (VideoLAN)". Não use "VLC" como nome,
  ícone ou palavra-chave principal.
- **Licenças:** o pacote leva o libVLC e os módulos do VLC (LGPL/GPL) e o Qt (LGPL), sem modificação.
  Os textos de licença vão na pasta `LICENSES`. Na listagem ou no site, informe onde está o código-fonte
  (<https://code.videolan.org/videolan/vlc>, versão 3.0.x).
- **Política de privacidade:** a Store pede uma URL quando o app acessa a internet. Uma página simples
  basta, dizendo que o Refrata não coleta dados.

## Testar o pacote neste PC

O pacote de teste (`-dev.msix`) é assinado com o certificado de desenvolvimento:

```powershell
python tools/test_msix.py --trust     # UAC: confia no certificado e roda o Kit de Certificação da Store
python tools/test_msix.py             # instala, roda os 44 testes dentro do pacote, abre um arquivo, desinstala
python tools/test_msix.py --untrust   # UAC: remove a confiança
```

O Windows não aceita pacotes sem assinatura que contenham executáveis (erro 0x80073D2B), por isso o
pacote de teste é assinado.
