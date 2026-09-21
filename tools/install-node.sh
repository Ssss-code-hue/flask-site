#!/bin/bash
# Установка узла Marzban для IKK VPN — по рабочей схеме Нидерландов.
#   * Docker + marzban-node с автоперезапуском (restart: always);
#   * ядро xray закреплено на версии мастера: REALITY отбивает рукопожатие,
#     если версии расходятся, а образ latest приносит другую;
#   * открыты порты 443 (XHTTP), 2083 (TCP — без него iPhone без серверов),
#     62050/62051 (связь с панелью) и 22 (SSH).
# Запуск:  bash install_node.sh
set -e
XRAY_VER="26.3.27"

echo "== 1/5 Docker"
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
command -v unzip >/dev/null || (apt-get update -qq && apt-get install -y -qq unzip)

echo "== 2/5 Сертификат панели"
mkdir -p /var/lib/marzban-node /var/lib/marzban/xray-core /opt/Marzban-node
cat > /var/lib/marzban-node/ssl_client_cert.pem <<'CERT'
-----BEGIN CERTIFICATE-----
MIIEnDCCAoQCAQAwDQYJKoZIhvcNAQENBQAwEzERMA8GA1UEAwwIR296YXJnYWgw
IBcNMjYwNzE4MTI0NzQ2WhgPMjEyNjA2MjQxMjQ3NDZaMBMxETAPBgNVBAMMCEdv
emFyZ2FoMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAtXT67IDOmcoM
uZxpwIgC5P28+3utIvM71MUOfHYHpU/ZNIdUjNGzoHydrNPdH2HU7TlLPZb8+sD4
LrFPErCR3emgldaMt5pwbM7va9gjxphSay4jCAE5izQ5rMjRS7sENS4mgvA9k9+1
hAo8Y3zxWzdqWppKdoZ90Ls9let9Mx5w4p62YmOyuYu11AwmTD2w3NNWkLrinRRy
60btOmfLhL2dH9TjwKyoz5l9sR79V2Qdgee39fwDGpCPL6loDmotaWwKWgVfkIFk
AcCLnexwwcc/JowwZ6LE2M5AK4YRbEZnsJVRYbENZRybZV8EyKgwVKqR5TLjElvU
tKDx8Jceg2rK5a4IXp0YOrTDyuTLlqqF9YhGGz4yBa2H2BQlJDL1qbmbnchSXPgx
3S1KFydNWQgJlvKT0db/hooHocWjmMBbmd3ffC9bkjypjBZZJVGAusFpLdekFSK7
YokwmnClf6HTIVX/pGzVPtuGqCvxYqR9nMv7dALC6dJ3Oj42MxFf0ysRM20ucvAA
tXMEloaPg5EjWKRk81IIPForhmMfAfv8pU8Q0qNPonJub1lprJF6MAarcJmyLcRI
+Jgqr9Xf0rpcnLmqpYWrCq1f0vGHxWIRrujkxHK9lvKBs5nYr+t1j6pl8/f/b4Ew
Ly3v4z8cO7XmxKmCQM67w6vhoBj2vSUCAwEAATANBgkqhkiG9w0BAQ0FAAOCAgEA
msEt8p/2WY37pNNS1KqihFqbmsmafyLYwzg1kQoLZiKnOlREJewuiG/dQGxmskMH
s5QjpAqpCS3kg/5rVNFRNN+sL3z/fXaVHG5XaNzrFL9i6Za3s998cn8EXfHjHhQr
K8hgMSoVFjPH15Ppt3NasEnKZxmWx6xgV6ZI8PY+GWm+nBWsxv+5clGeFEIFzN1H
YyBfaf7Z4yB0vt1MBFJnHgvo58Pi4BNO5159K0tw76pWoi/c35O7DwDcOzYyff25
oJq/6FvoQIwdTJ3BsaU0sX5hnOsQPfX6ypoh+HRZ6uKLTGEB/2PSFVxd1f1klB/q
rBqYrgHgrMyBOpMLOIpqNjvgQOxyfDPw13Q2oQE+MFSb+/CEgrc/vFM1IOZibt61
xLm3MfYRatfLjPD9upvhlQfq8zaf36qVNUSbKu0JC52SWOzlCB/zaoowpNgaS3/n
0bNZR9ryYxTmxt2h/NkjqQ/q6YQhVClqKLcdXwpBP+s1mywbv0hnPS/XhUdqFjUr
JJyxb4EAVVwwNX55Mj1uqOtK47oWnM7AieDHn23joCosaEK0xgmT7OfClGi5vNJx
/HiFspQOMJFTzfoGGajrME6txLZkn0nKW1Nw6D3aVPGLQZdlg9liNevyknD90Rte
z+84qKqfhprpGj0ActZ7mhu06Not3nYm7geWcygOSP4=
-----END CERTIFICATE-----
CERT

echo "== 3/5 Ядро xray $XRAY_VER (как у мастера)"
cd /var/lib/marzban/xray-core
curl -fsSL -o xray.zip "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VER}/Xray-linux-64.zip"
unzip -o -q xray.zip && chmod +x xray && rm -f xray.zip
./xray version | head -1

echo "== 4/5 Узел Marzban"
cat > /opt/Marzban-node/docker-compose.yml <<'YML'
services:
  marzban-node:
    image: gozargah/marzban-node:latest
    restart: always
    network_mode: host
    environment:
      SSL_CLIENT_CERT_FILE: "/var/lib/marzban-node/ssl_client_cert.pem"
      SERVICE_PROTOCOL: "rest"
      XRAY_EXECUTABLE_PATH: "/var/lib/marzban/xray-core/xray"
    volumes:
      - /var/lib/marzban-node:/var/lib/marzban-node
      - /var/lib/marzban:/var/lib/marzban
YML
cd /opt/Marzban-node && docker compose pull -q && docker compose up -d

echo "== 5/5 Файрвол"
if command -v ufw >/dev/null; then
  for p in 22 443 2083 62050 62051; do ufw allow ${p}/tcp >/dev/null; done
  ufw --force enable >/dev/null
  ufw status | grep -E "22|443|2083|6205"
else
  echo "ufw не установлен — порты не закрыты, ничего открывать не нужно"
fi

sleep 5
echo
docker ps --format '{{.Names}}  {{.Status}}'
echo "ГОТОВО. Напишите в чат — узел подключу к панели."
