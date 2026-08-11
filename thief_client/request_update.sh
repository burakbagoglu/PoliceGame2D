#!/usr/bin/env sh
set -eu

# Bu helper root tarafindan kurulur ve sudoers yalniz bu dosyayi parolasiz
# calistirmaya izin verir. Disaridan komut veya arguman kabul etmez.
[ "$#" -eq 0 ] || exit 64
exec systemctl start --no-block thief-game-update.service
