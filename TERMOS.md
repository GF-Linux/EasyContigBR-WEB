# Termos de uso — EasyContig BR (serviço web)

> **MINUTA.** Escrita em 2026-08-08 para ser revista pelo NIT/UFRRJ. Descreve o
> que o serviço **faz hoje**, verificado no código e na instância em produção —
> não o que se pretende que ele faça. Onde a política ainda não foi decidida,
> está escrito "EM ABERTO" em vez de uma promessa inventada.

O `LICENSE` governa **quem copia o código**. Este documento governa **quem
confia dado ao serviço** — o laboratório que envia `.ab1` para
`easycontigbr.com.br`. São relações diferentes, com leitores diferentes.

---

## 1. Quem pode entrar

O acesso é por conta Google, e só entram endereços na lista declarada pela
instalação. Hoje: qualquer conta `@ufrrj.br`, mais dois endereços nomeados
(os responsáveis). A tela de entrada anuncia o domínio aceito.

⚠️ O Google prova **quem** é a pessoa; ele **não** restringe quem entra. Quem
restringe é essa lista, e só ela.

## 2. O que o serviço guarda

- Os arquivos `.ab1`/`.abi`/`.scf` **como enviados**;
- O que o processamento produz: consenso, relatório (HTML/JSON/CSV), traço;
- O estado da corrida (fila, contagens, cota) num banco SQLite;
- O que a pessoa **declara** no perfil: nome, laboratório, instituição,
  descrição, espécies, marcadores, endereços de portfólio e foto.

Não há coleta de nada além disso — sem rastreadores, sem análise de uso, sem
cookies de terceiros. O único cookie é o de sessão.

## 3. Por quanto tempo

- **Corridas: 30 dias.** Depois disso o expurgo automático apaga os arquivos e
  o relatório. Não é arquivo permanente — quem quiser guardar, baixa.
- **Backup: mais 30 dias.** Cópia diária às 03:15, com a mesma retenção. Ou
  seja, um dado enviado pode existir em cópia por até ~60 dias após o envio.
- Uma cópia do backup é puxada para uma máquina do responsável, fora da VPS.

## 4. Onde o dado mora, e o que isso implica

O serviço roda numa **VPS comercial contratada (Locaweb), no Brasil**. Não é
infraestrutura da UFRRJ.

⚠️ **O disco não é cifrado em repouso.** Quem tem acesso administrativo à
máquina — o responsável técnico e, em tese, o fornecedor — alcança os arquivos.
Isso é uma característica da hospedagem escolhida, e está escrito aqui porque
quem envia sequenciamento não publicado tem o direito de saber antes de enviar.

O serviço foi desenhado para que migrar seja copiar uma pasta e trocar um
registro de DNS — não há banco gerenciado nem armazenamento de terceiro onde o
dado passe a morar fora do nosso alcance.

## 5. O que sai do servidor, e o que não sai

**Não sai:** as suas sequências. Nem para o NCBI, nem para lugar nenhum. O
BLAST roda **localmente**, contra bancos que já estão no servidor. O módulo
capaz de enviar uma sequência ao NCBI existe no código do projeto e **não é
usado no caminho web** (verificado em 2026-08-08).

**Sai:** o seu e-mail vai ao Google no momento do login, porque é ele quem
autentica. E o servidor **baixa** do NCBI sequências de referência públicas
quando um banco é montado — é uma consulta por táxon e marcador, nunca contém
dado seu.

## 6. O que outras pessoas do serviço veem

Desde 2026-08-08, **entrar no site já cadastra a pessoa no diretório `/labs`**,
visível a qualquer outra pessoa autenticada. O que aparece:

- o nome que a pessoa declarou no perfil — ou, se não declarou, o nome da conta
  Google dela; sem nenhum dos dois, o cartão diz "perfil sem nome";
- laboratório, instituição, descrição, espécies, marcadores e portfólio, **se
  declarados**;
- a foto, se enviada.

**Nunca aparece:** o seu e-mail, os nomes das suas corridas, os seus arquivos,
nem o que o BLAST achou. O diretório mostra declaração, nunca atividade.

⚠️ **EM ABERTO:** hoje não há como sair do diretório sem deixar de usar o
serviço. Se a lista deve ser opcional é decisão do NIT.

## 7. O que o resultado é — e o que não é

O EasyContig BR monta contigs a partir de leituras Sanger e sugere identificação
comparando com bancos de referência. É **instrumento de apoio à pesquisa**.

- **Não é diagnóstico.** Não substitui laudo, nem decisão clínica, nem
  conclusão de quem assina o trabalho.
- **Não valida a escolha da referência.** Se o banco escolhido não contém o
  organismo, "sem acerto" significa "não está neste banco" — e não "não é isso".
- A responsabilidade científica pela interpretação é de quem publica.

## 8. Sem garantia

O serviço é oferecido **como está**, sem garantia de disponibilidade,
integridade ou adequação a qualquer finalidade. É mantido por uma equipe de
laboratório, não por uma operação com plantão. Guarde cópia do que você enviar.

## 9. O que ainda não foi decidido (para o NIT)

Estas perguntas estão abertas de propósito. Inventar resposta aqui seria pior
que registrar a lacuna:

- **Quem pode apagar o dado de quem?** Hoje cada conta apaga só o que é dela.
  Não há administrador com poder de apagar dado alheio, nem procedimento para
  quando alguém deixa o laboratório.
- **O que acontece quando a pessoa sai da UFRRJ?** A conta perde acesso pelo
  portão de domínio, mas o dado dela continua até o expurgo dos 30 dias.
- **Quem responde por um incidente**, e em quanto tempo os envolvidos são
  avisados.
- **Quem é o controlador** dos dados para efeito de LGPD — o pesquisador, o
  laboratório ou a universidade —, e se sequenciamento de amostra animal com
  metadado de origem exige tratamento próprio.
- Se e como o serviço se abre para fora da UFRRJ, que é a pergunta que a
  PolyForm cobre mal e motivou levar isto ao NIT.

## 10. Mudanças

Este documento muda junto com o serviço. O histórico fica no repositório —
cada alteração é um commit datado, e não uma versão que substitui a anterior em
silêncio.

---

**Contato:** Gustavo Gonçalves Freitas — LHV/UFRRJ.
**Última verificação contra o código em produção:** 2026-08-08.
