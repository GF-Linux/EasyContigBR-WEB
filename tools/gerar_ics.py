"""
gerar_ics.py — escreve o .ics das datas de operacao do EasyContig BR, para importar no Google Calendar.

Cada evento leva VALARM (e o que vira notificacao) e uma descricao que diz o
QUE conferir e COM QUAL comando: calendario que so diz "renovar certificado"
manda a pessoa procurar de novo o que ela ja tinha descoberto.

Datas conferidas na fonte, nao na anotacao:

    certificado   openssl s_client -> notAfter=Nov  5 18:34:08 2026 GMT
    dominio       RDAP registro.br -> expiration 2027-08-06T16:40:57Z


COMO FUNCIONA
=============

    EVENTOS  (uid, dia, hora, titulo, descricao, rrule, gatilhos de alarme)
       |
       |-- linhas_do_evento(evento)      BEGIN:VEVENT ... END:VEVENT, com um VALARM por gatilho
       |-- montar_calendario()           VCALENDAR + VTIMEZONE (America/Sao_Paulo, -0300 o ano inteiro)
       |                                 + os eventos + END:VCALENDAR
       |-- escapar(texto)                barra, ponto-e-virgula, virgula e quebra de linha ganham a barra de escape do iCalendar
       '-- dobrar(linha)                 linha acima de 73 octetos quebra em 72, continuacao com espaco
                                         (o Google tolera linha longa; outros clientes nao)
    main()
       grava as linhas com CRLF no destino e diz quantos eventos e linhas sairam


COMO USAR
=========

    python3 tools/gerar_ics.py                      # easycontig-datas.ics na pasta atual
    python3 tools/gerar_ics.py ~/Desktop/datas.ics  # noutro lugar


O QUE MUDOU DO ORIGINAL
=======================

 1. O destino era fixo em /home/deck/Desktop/easycontig-datas.ics (a maquina de
    quem escreveu). Agora e o argumento opcional da linha de comando, e sem
    argumento sai easycontig-datas.ics na pasta atual.
 2. O roteiro montava tudo no nivel do modulo; agora e main() e funcoes, no
    estilo da casa. O conteudo do .ics e identico ao do original, byte a byte.


POR QUE AQUI NAO NASCE CLASSE
=============================

Uma lista de eventos vira um texto: nao ha estado entre chamadas. Funcoes puras
bastam.
"""

from pathlib import Path

import sys




CARIMBO = '20260808T200000Z'  #* DTSTAMP de todos os eventos: quando o calendario foi escrito

FUSO = 'America/Sao_Paulo'

DOMINIO = 'easycontigbr.com.br'

DESTINO_PADRAO = 'easycontig-datas.ics'

EVENTOS = [  #* (uid, dia, hora, titulo, descricao, rrule, [gatilhos de alarme])
    ('cert-janela-2026', '20261006', '0900',
     'EasyContig · Certificado deve renovar sozinho — conferir',
     'O certbot renova a 30 dias do vencimento, e o certificado atual vence em '
     '05/11/2026. A partir de hoje a renovação deve acontecer sozinha.\n\n'
     'CONFERIR daqui a uns dias:\n'
     '  echo | openssl s_client -connect easycontigbr.com.br:443 '
     '-servername easycontigbr.com.br 2>/dev/null | openssl x509 -noout -dates\n\n'
     'Se o notAfter NÃO tiver avançado, ver o timer:\n'
     "  ssh vps-easycontig 'systemctl list-timers certbot* --all'\n\n"
     '⚠️ O ponto frágil está na §7 do checklist: o desafio ACME tem de ficar '
     'FORA do redirecionamento http→https, senão a RENOVAÇÃO quebra sem '
     'ninguém olhando.',
     None, ['-P2D', 'PT0S']),

    ('cert-prazo-2026', '20261103', '0900',
     'EasyContig · ⚠️ PRAZO: certificado TLS vence em 05/11',
     'Faltam 2 dias. Se o certificado ainda não renovou, agir AGORA — '
     'certificado vencido derruba o site inteiro para o laboratório.\n\n'
     "  ssh vps-easycontig 'certbot renew --dry-run'   (ensaio, não renova)\n"
     "  ssh vps-easycontig 'certbot renew'             (renova de verdade)\n"
     "  ssh vps-easycontig 'nginx -t && systemctl reload nginx'\n\n"
     'O UptimeRobot também pega isto — mas só quando JÁ quebrou.',
     None, ['-P7D', 'PT0S']),

    ('dominio-aviso-2027', '20270705', '0900',
     'EasyContig · Renovar o domínio easycontigbr.com.br (1 mês)',
     'Expira em 05/08/2027 (painel do Registro.br). Renovação é '
     'anual e paga.\n\n'
     '  https://registro.br  →  titular Gustavo Freitas\n\n'
     '⚠️ Domínio vencido não é só o site fora: é o registro A que aponta para '
     '191.252.229.238 sumindo, e o login pelo Google quebrando junto (a URI de '
     'redirecionamento é cadastrada no domínio raiz).\n\n'
     'Conferir a data a qualquer momento:\n'
     '  curl -s https://rdap.registro.br/domain/easycontigbr.com.br',
     None, ['-P7D', 'PT0S']),

    ('dominio-prazo-2027', '20270802', '0900',
     'EasyContig · ⚠️ PRAZO: domínio expira em 05/08/2027',
     'Faltam 3 dias. Depois do vencimento há período de graça, mas o site sai '
     'do ar antes disso.',
     None, ['PT0S']),

    ('vps-salto-preco', '20260903', '0900',
     'EasyContig · \u26a0 A fatura de 06/09 sobe para R$ 90/mês',
     'A VPS Locaweb sai de R$ 53,90 para R$ 90,00 por mês a partir desta '
     'fatura — +67%, e daí em diante mês a mês.\n\n'
     'R$ 90/mês são R$ 1.080/ano. Vale saber ANTES da fatura chegar, porque '
     'é a hora de decidir se continua assim ou se procura outra coisa — e não '
     'depois, com o serviço já no ar e o laboratório usando.\n\n'
     'Painel CloudStack, conta LOCAWEB-jaredbr, VM EasyContigVM.',
     None, ['PT0S']),

    ('vps-cobranca', '20260906', '0900',
     'EasyContig · Fatura da VPS Locaweb (R$ 90/mês)',
     'Vence todo dia 6. R$ 90,00/mês desde a fatura de 06/09/2026 (antes era '
     'R$ 53,90 — ver o evento de 03/09).\n\n'
     'Painel CloudStack, conta LOCAWEB-jaredbr, VM EasyContigVM.\n\n'
     '\u26a0 VPS suspensa por falta de pagamento não é só o site fora: é o '
     '/var/lib/easycontig inacessível, e o backup do PC passa a ser a única '
     'cópia que existe.',
     'FREQ=MONTHLY;BYMONTHDAY=6', ['-P2D', 'PT0S']),

    ('backup-chegou', '20260901', '0930',
     'EasyContig · O backup está chegando no PC?',
     'Item aberto da §8 do checklist: conferir uma vez por mês se a cópia está '
     'chegando em C:\\EasyContigBackup (puxada pelo WSL via Tailscale).\n\n'
     'Na VPS, ver se o de lá rodou:\n'
     "  ssh vps-easycontig 'ls -la /backup'\n"
     "  ssh vps-easycontig 'tail -20 /var/log/easycontig-backup.log'\n\n"
     'Backup que roda na VPS e não chega no PC protege contra apagão, não '
     'contra a VPS sumir.',
     'FREQ=MONTHLY;BYMONTHDAY=1', ['PT0S']),

    ('backup-restaurar', '20261107', '1000',
     'EasyContig · Exercitar a RESTAURAÇÃO do backup (trimestral)',
     "A pergunta da §8 não é 'o backup está ligado', é 'EU JÁ RESTAUREI?'.\n\n"
     'Exercitado em 06/08 e 07/08/2026. Refazer a cada trimestre — restauração '
     'que nunca foi testada é esperança, não backup.\n\n'
     '⚠️ SQLite em modo WAL não se copia com `cp` com o servidor rodando: '
     'metade do estado vive no -wal, e a cópia parece íntegra estando '
     'incompleta. O script usa VACUUM INTO por isso.',
     'FREQ=MONTHLY;INTERVAL=3', ['-P1D', 'PT0S']),
]




def dobrar(linha):

    #* o iCalendar manda dobrar linha acima de 75 octetos; a continuacao comeca com um espaco

    if len(linha.encode('utf-8')) <= 73:
        return [linha]

    pedacos = []

    atual = ''

    for caractere in linha:

        if len((atual + caractere).encode('utf-8')) > 72:
            pedacos.append(atual)

            atual = ' ' + caractere

        else:
            atual += caractere

    pedacos.append(atual)

    return pedacos




def escapar(texto):

    return texto.replace('\\', '\\\\').replace(';', r'\;').replace(',', r'\,').replace('\n', r'\n')




def linhas_do_evento(evento):

    uid, dia, hora, titulo, descricao, rrule, alarmes = evento

    linhas = [
        'BEGIN:VEVENT',
        f'UID:{uid}@{DOMINIO}',
        f'DTSTAMP:{CARIMBO}',
        f'DTSTART;TZID={FUSO}:{dia}T{hora}00',
        f'DTEND;TZID={FUSO}:{dia}T{int(hora[:2]) + 1:02d}{hora[2:]}00',  #* uma hora de duracao
        f'SUMMARY:{escapar(titulo)}',
        f'DESCRIPTION:{escapar(descricao)}',
        'TRANSP:TRANSPARENT',
    ]

    if rrule:
        linhas.append(f'RRULE:{rrule}')

    for gatilho in alarmes:
        linhas += ['BEGIN:VALARM', 'ACTION:DISPLAY', f'DESCRIPTION:{escapar(titulo)}', f'TRIGGER:{gatilho}', 'END:VALARM']

    linhas.append('END:VEVENT')

    return linhas




def montar_calendario():

    linhas = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//EasyContig BR//datas de operacao//PT-BR',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:EasyContig BR — operação',
        'BEGIN:VTIMEZONE',  #* o Brasil acabou com o horario de verao em 2019: America/Sao_Paulo e UTC-3 o ano inteiro, um bloco STANDARD basta
        f'TZID:{FUSO}',
        'BEGIN:STANDARD',
        'DTSTART:19700101T000000',
        'TZOFFSETFROM:-0300',
        'TZOFFSETTO:-0300',
        'TZNAME:-03',
        'END:STANDARD',
        'END:VTIMEZONE',
    ]

    for evento in EVENTOS:
        linhas += linhas_do_evento(evento)

    linhas.append('END:VCALENDAR')

    saida = []

    for linha in linhas:
        saida.extend(dobrar(linha))

    return saida




def main():

    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DESTINO_PADRAO)

    saida = montar_calendario()

    destino.parent.mkdir(parents=True, exist_ok=True)

    destino.write_bytes(('\r\n'.join(saida) + '\r\n').encode('utf-8'))

    print(f'{destino} — {len(EVENTOS)} eventos, {len(saida)} linhas')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
