"""Gera o .ics das datas do EasyContig BR para importar no Google Calendar.

Cada evento leva VALARM (é o que vira notificação) e uma descrição que diz o
QUE conferir e COM QUAL comando — calendário que só diz "renovar certificado"
manda a pessoa procurar de novo o que ela já tinha descoberto.

Datas conferidas na fonte, não na anotação:
  certificado  openssl s_client → notAfter=Nov  5 18:34:08 2026 GMT
  domínio      RDAP registro.br → expiration 2027-08-06T16:40:57Z
"""
CARIMBO = "20260808T200000Z"
FUSO = "America/Sao_Paulo"

# (uid, dia, hora, titulo, descricao, rrule, [gatilhos de alarme])
EVENTOS = [
    ("cert-janela-2026", "20261006", "0900",
     "EasyContig · Certificado deve renovar sozinho — conferir",
     "O certbot renova a 30 dias do vencimento, e o certificado atual vence em "
     "05/11/2026. A partir de hoje a renovação deve acontecer sozinha.\n\n"
     "CONFERIR daqui a uns dias:\n"
     "  echo | openssl s_client -connect easycontigbr.com.br:443 "
     "-servername easycontigbr.com.br 2>/dev/null | openssl x509 -noout -dates\n\n"
     "Se o notAfter NÃO tiver avançado, ver o timer:\n"
     "  ssh vps-easycontig 'systemctl list-timers certbot* --all'\n\n"
     "⚠️ O ponto frágil está na §7 do checklist: o desafio ACME tem de ficar "
     "FORA do redirecionamento http→https, senão a RENOVAÇÃO quebra sem "
     "ninguém olhando.",
     None, ["-P2D", "PT0S"]),

    ("cert-prazo-2026", "20261103", "0900",
     "EasyContig · ⚠️ PRAZO: certificado TLS vence em 05/11",
     "Faltam 2 dias. Se o certificado ainda não renovou, agir AGORA — "
     "certificado vencido derruba o site inteiro para o laboratório.\n\n"
     "  ssh vps-easycontig 'certbot renew --dry-run'   (ensaio, não renova)\n"
     "  ssh vps-easycontig 'certbot renew'             (renova de verdade)\n"
     "  ssh vps-easycontig 'nginx -t && systemctl reload nginx'\n\n"
     "O UptimeRobot também pega isto — mas só quando JÁ quebrou.",
     None, ["-P7D", "PT0S"]),

    ("dominio-aviso-2027", "20270706", "0900",
     "EasyContig · Renovar o domínio easycontigbr.com.br (1 mês)",
     "Expira em 06/08/2027 (conferido no RDAP do Registro.br). Renovação é "
     "anual e paga.\n\n"
     "  https://registro.br  →  titular Gustavo Freitas\n\n"
     "⚠️ Domínio vencido não é só o site fora: é o registro A que aponta para "
     "191.252.229.238 sumindo, e o login pelo Google quebrando junto (a URI de "
     "redirecionamento é cadastrada no domínio raiz).\n\n"
     "Conferir a data a qualquer momento:\n"
     "  curl -s https://rdap.registro.br/domain/easycontigbr.com.br",
     None, ["-P7D", "PT0S"]),

    ("dominio-prazo-2027", "20270803", "0900",
     "EasyContig · ⚠️ PRAZO: domínio expira em 06/08/2027",
     "Faltam 3 dias. Depois do vencimento há período de graça, mas o site sai "
     "do ar antes disso.",
     None, ["PT0S"]),

    ("vps-cobranca", "20260901", "0900",
     "EasyContig · Conferir a cobrança da VPS Locaweb",
     "R$ 53,90/mês, que RENOVA A R$ 72,90 — o aumento vem, e é bom não ser "
     "surpresa (§1 do checklist).\n\n"
     "⚠️ DATA A CONFIRMAR: eu não consegui determinar o dia da cobrança pela "
     "máquina (o 'Filesystem created' de fevereiro é do template, não do "
     "provisionamento). Ajuste este evento para o dia real da fatura, e me "
     "diga quando o preço promocional termina que eu acrescento o evento.\n\n"
     "Painel CloudStack, conta LOCAWEB-jaredbr, VM EasyContigVM.",
     "FREQ=MONTHLY;BYMONTHDAY=1", ["PT0S"]),

    ("backup-chegou", "20260901", "0930",
     "EasyContig · O backup está chegando no PC?",
     "Item aberto da §8 do checklist: conferir uma vez por mês se a cópia está "
     "chegando em C:\\EasyContigBackup (puxada pelo WSL via Tailscale).\n\n"
     "Na VPS, ver se o de lá rodou:\n"
     "  ssh vps-easycontig 'ls -la /backup'\n"
     "  ssh vps-easycontig 'tail -20 /var/log/easycontig-backup.log'\n\n"
     "Backup que roda na VPS e não chega no PC protege contra apagão, não "
     "contra a VPS sumir.",
     "FREQ=MONTHLY;BYMONTHDAY=1", ["PT0S"]),

    ("backup-restaurar", "20261107", "1000",
     "EasyContig · Exercitar a RESTAURAÇÃO do backup (trimestral)",
     "A pergunta da §8 não é 'o backup está ligado', é 'EU JÁ RESTAUREI?'.\n\n"
     "Exercitado em 06/08 e 07/08/2026. Refazer a cada trimestre — restauração "
     "que nunca foi testada é esperança, não backup.\n\n"
     "⚠️ SQLite em modo WAL não se copia com `cp` com o servidor rodando: "
     "metade do estado vive no -wal, e a cópia parece íntegra estando "
     "incompleta. O script usa VACUUM INTO por isso.",
     "FREQ=MONTHLY;INTERVAL=3", ["-P1D", "PT0S"]),
]


def dobrar(linha: str) -> list[str]:
    """iCalendar manda dobrar linha acima de 75 octetos; a continuação começa
    com um espaço. Google tolera linha longa, mas outros clientes não."""
    b = linha.encode("utf-8")
    if len(b) <= 73:
        return [linha]
    pedacos, atual = [], ""
    for ch in linha:
        if len((atual + ch).encode("utf-8")) > 72:
            pedacos.append(atual)
            atual = " " + ch
        else:
            atual += ch
    pedacos.append(atual)
    return pedacos


def escapar(t: str) -> str:
    return (t.replace("\\", "\\\\").replace(";", r"\;")
             .replace(",", r"\,").replace("\n", r"\n"))


L = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//EasyContig BR//datas de operacao//PT-BR",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "X-WR-CALNAME:EasyContig BR — operação",
    # O Brasil acabou com o horário de verão em 2019, então America/Sao_Paulo é
    # UTC-3 o ano inteiro e um bloco STANDARD sozinho basta.
    "BEGIN:VTIMEZONE",
    f"TZID:{FUSO}",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:-0300",
    "TZOFFSETTO:-0300",
    "TZNAME:-03",
    "END:STANDARD",
    "END:VTIMEZONE",
]

for uid, dia, hora, titulo, desc, rrule, alarmes in EVENTOS:
    L += [
        "BEGIN:VEVENT",
        f"UID:{uid}@easycontigbr.com.br",
        f"DTSTAMP:{CARIMBO}",
        f"DTSTART;TZID={FUSO}:{dia}T{hora}00",
        f"DTEND;TZID={FUSO}:{dia}T{int(hora[:2]) + 1:02d}{hora[2:]}00",
        f"SUMMARY:{escapar(titulo)}",
        f"DESCRIPTION:{escapar(desc)}",
        "TRANSP:TRANSPARENT",
    ]
    if rrule:
        L.append(f"RRULE:{rrule}")
    for gatilho in alarmes:
        L += ["BEGIN:VALARM", "ACTION:DISPLAY",
              f"DESCRIPTION:{escapar(titulo)}",
              f"TRIGGER:{gatilho}", "END:VALARM"]
    L.append("END:VEVENT")

L.append("END:VCALENDAR")

saida = []
for linha in L:
    saida.extend(dobrar(linha))

import pathlib
destino = pathlib.Path("/home/deck/Desktop/easycontig-datas.ics")
destino.write_bytes(("\r\n".join(saida) + "\r\n").encode("utf-8"))
print(f"{destino} — {len(EVENTOS)} eventos, {len(saida)} linhas")
