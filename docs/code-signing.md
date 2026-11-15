# Assinatura gratuita (SignPath Foundation)

Sem assinatura confiável, o Windows mostra o aviso azul do SmartScreen ("Windows protegeu o
computador") e o navegador desconfia do download. Projetos de **código aberto** podem receber um
certificado de assinatura de graça pela [SignPath Foundation](https://signpath.org). O certificado
fica em nome da fundação, e o projeto assina pelo serviço da SignPath a partir do build do GitHub
Actions.

## Requisitos (resumo; confira a lista atual em signpath.org)

- licença aprovada pela OSI: o Refrata usa **GPL-3.0** ([LICENSE](../LICENSE));
- código público e mantido: <https://github.com/LucsLabsStudios/refrata>;
- os binários assinados saem de um **build automatizado** a partir do código público
  ([.github/workflows/build.yml](../.github/workflows/build.yml)), não de uma máquina pessoal;
- uma **política de assinatura** publicada no site do projeto (seção "Assinatura de código" em
  <https://refrata.vercel.app/#seguranca>);
- autenticação em dois fatores (2FA) nas contas do GitHub de quem pode publicar;
- nada de malware, adware ou instalação de software extra.

## Passo a passo

1. Ative o 2FA na conta do GitHub (Settings › Password and authentication).
2. Preencha o formulário em <https://signpath.org/apply> com:
   - nome: Refrata · repositório: `https://github.com/LucsLabsStudios/refrata`;
   - site: `https://refrata.vercel.app`;
   - descrição: player de mídia para Windows construído sobre o libVLC;
   - artefatos: `Refrata.exe`, `Refrata-Setup-<versão>.exe` (Inno Setup) e o desinstalador.
3. Depois da aprovação, a SignPath manda o ID da organização e do projeto. Instale o app
   **SignPath** no repositório do GitHub e crie os segredos do repositório
   `SIGNPATH_API_TOKEN` e a variável `SIGNPATH_ORGANIZATION_ID`.
4. Descomente o passo "Assinar com a SignPath" em `.github/workflows/build.yml` (usa a action
   oficial `signpath/github-action-submit-signing-request`).
5. A partir daí, o lançamento usa os arquivos assinados que o workflow gera, no lugar dos
   montados localmente.

## Enquanto isso

- O app funciona normalmente; só aparecem os avisos no primeiro download e na instalação.
- O site explica como passar pelo aviso ("Mais informações" › "Executar assim mesmo").
- As atualizações feitas pelo próprio app conferem o **SHA-256** publicado no lançamento antes de
  instalar qualquer coisa.
