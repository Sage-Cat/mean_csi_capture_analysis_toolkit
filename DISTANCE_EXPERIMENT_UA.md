# Експеримент відстані (Distance) — інструкція українською

## 1) Мета

Експеримент `distance` збирає CSI/RSSI дані для відомих дистанцій між TX і RX, щоб далі оцінювати відстань моделями.

- Тип: `distance`
- Поточний профіль середовища: `esp32s3_csi_v1`
- Ground-truth: `distance_m`

## 2) Сетап

Потрібно:

- Ноутбук A + ESP32 TX (`csi_send`)
- Ноутбук B + ESP32 RX (`csi_recv`)
- рулетка/мітки на підлозі для точних дистанцій

Рекомендовано фіксувати:

- висоту TX/RX
- орієнтацію плат
- однаковий канал/умови в межах одного експерименту

## 3) Підготовка середовища

На обох ноутбуках:

```bash
cd csi_capturing_example
python3 -m pip install -r requirements.txt
```

Перевірити доступність портів:

```bash
./tools/exp --list-devices
./tools/exp --list-target-profiles
```

## 4) Запуск TX (Laptop A)

```bash
cd csi_capturing_example
./scripts/run_tx_laptop.sh --skip-build --skip-flash
```

За потреби явно вкажи порт:

```bash
./scripts/run_tx_laptop.sh --port /dev/cu.usbmodem2101 --skip-build --skip-flash
```

## 5) Варіант A: швидкий запис через `run_rx_laptop.sh`

```bash
cd csi_capturing_example
./scripts/run_rx_laptop.sh \
  --target-profile esp32s3_csi_v1 \
  --port /dev/esp32_csi \
  --exp-id exp_distance_20260302 \
  --scenario LoS \
  --run-id 1 \
  --distance-m 1.0 \
  --max-records 2500 \
  --skip-build --skip-flash
```

Для серії дистанцій повторюй команду, змінюючи:

- `--distance-m`
- `--run-id`

## 6) Варіант B: config-driven runner (рекомендовано для повторюваності)

1. Візьми шаблон: `docs/configs/distance_capture.sample.json`
2. Онови `exp_id`, `distances_m`, `repeats_per_distance`, `scenario_tags`
3. Запусти:

```bash
cd csi_capturing_example
python3 -m csi_capture.experiment distance \
  --config docs/configs/distance_capture.sample.json \
  --target-profile esp32s3_csi_v1 \
  --device auto
```

## 7) Протокол вимірювання

1. Розміть позиції (наприклад: `1.0m, 1.5m, 2.0m, 3.0m`).
2. Для кожної дистанції зроби мінімум 2 повтори.
3. Перед стартом кожного trial перевір, що між TX і RX немає людей.
4. Зафіксуй сценарій: `LoS`, `NLoS`, `multipath` тощо.

## 8) Де лежать результати

Legacy-скрипт:

- `experiments/<exp_id>/meta.json`
- `experiments/<exp_id>/<scenario>/run_<id>/distance_<X>m.jsonl`
- `experiments/<exp_id>/<scenario>/run_<id>/manifest.json`

Config-driven runner:

- `experiments/<exp_id>/distance/run_<run_id>/manifest.json`
- `experiments/<exp_id>/distance/run_<run_id>/trial_distance_*/*`

У `manifest.json` зберігаються:

- `target_profile`
- `environment_profile`
- `git_commit`
- `config_snapshot`

## 9) Перевірка даних

```bash
find experiments/<exp_id> -name "*.jsonl" -exec wc -l {} \;
```

Аналіз:

```bash
python3 tools/analyze_wifi_distance_measurement.py \
  --data_dir experiments/<exp_id> \
  --out_dir out/distance_measurement
```

## 10) Швидкий чекліст

- TX запущений і стабільно передає.
- RX порт правильний (`/dev/esp32_csi` або явний `/dev/cu.usbmodem*`).
- Дистанції заміряні рулеткою, а не "на око".
- Для кожної дистанції є повтори.
- У маніфестах присутній `target_profile=esp32s3_csi_v1`.
