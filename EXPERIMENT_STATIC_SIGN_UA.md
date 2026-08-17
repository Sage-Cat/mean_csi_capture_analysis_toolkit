# Опис експерименту static_sign_v1 (UA)

## 1) Мета експерименту

Експеримент `static_sign_v1` призначений для бінарної класифікації статичного жесту людини за CSI-даними ESP32:

- `baseline` — нейтральна поза
- `hands_up` — руки вгору

Кінцева мета: навчити просту базову модель (лінійний SVM або логістична регресія), яка розрізняє ці два стани на основі статистичних ознак CSI.

Поточний спільний профіль середовища для експериментів: `esp32s3_csi_v1`.

---

## 2) Сетап (2 ноутбуки + 2 ESP32)

- **Ноутбук A (TX/AP)**: запускає ESP32 `csi_send` (джерело Wi‑Fi/CSI трафіку).
- **Ноутбук B (RX)**: запускає ESP32 `csi_recv` і виконує захоплення CSI у датасет.
- **Людина**: стоїть між TX і RX, **спиною до приймача**, показує статичний знак.

Рекомендація: не змінювати геометрію (позиції TX/RX/людини) в межах одного датасету.

---

## 3) Залежності та встановлення (Linux + macOS)

Нижче вказано все, що потрібно встановити для повного циклу:

- прошивка ESP32 (`idf.py build/flash`)
- захоплення CSI (`tools/exp capture`)
- навчання та оцінка моделі (`tools/exp train/eval`)

### 3.1 Python-залежності проєкту (обидві платформи)

Використовуються бібліотеки з `requirements.txt`:

- `pyserial` — читання серійного порту
- `numpy`, `pandas` — обробка даних
- `scikit-learn` — моделі класифікації
- `matplotlib` — графіки/аналітика

Рекомендовано ставити у `venv`.

### 3.2 Linux (Ubuntu/Debian): системні залежності

```bash
sudo apt update
sudo apt install -y \
  git wget flex bison gperf python3 python3-pip python3-venv python3-serial \
  cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0
```

Доступ до серійного порту:

```bash
sudo usermod -a -G dialout $USER
```

Після цього вийди з сесії та зайди знову.

### 3.3 macOS (Homebrew): системні залежності

```bash
brew update
brew install git wget python cmake ninja ccache dfu-util libusb pkg-config
```

Для macOS порти зазвичай:

- `/dev/cu.usbmodem*`
- `/dev/tty.usbmodem*`
- `/dev/cu.usbserial*`
- `/dev/tty.usbserial*`

### 3.4 Встановлення Python-оточення (обидві платформи)

```bash
cd csi_capturing_example
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### 3.5 ESP-IDF + esp-csi (обидві платформи)

```bash
mkdir -p ~/esp
cd ~/esp
git clone -b v5.5.3 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3
```

Зручно додати alias:

```bash
# Linux (bash)
echo "alias get_idf='. $HOME/esp/esp-idf/export.sh'" >> ~/.bashrc
source ~/.bashrc

# macOS (zsh)
echo "alias get_idf='. $HOME/esp/esp-idf/export.sh'" >> ~/.zshrc
source ~/.zshrc
```

Клонувати `esp-csi`:

```bash
cd ~/esp
git clone https://github.com/espressif/esp-csi.git
```

### 3.6 Перевірка встановлення

У каталозі проєкту:

```bash
cd csi_capturing_example
source .venv/bin/activate
python3 -m unittest discover -s tests -p 'test_*.py' -v
./tools/exp --help
./tools/exp --list-devices
```

---

## 4) Команди для запуску

### 4.1 Ноутбук A (TX/AP)

Перший запуск (зі збіркою/прошивкою):

```bash
cd csi_capturing_example
./scripts/run_tx_laptop.sh
```

Повторні запуски (без rebuild/reflash):

```bash
cd csi_capturing_example
./scripts/run_tx_laptop.sh --skip-build --skip-flash
```

Приклад з явним портом (актуально для macOS):

```bash
./scripts/run_tx_laptop.sh --port /dev/cu.usbmodem2101
```

### 4.2 Ноутбук B (RX + capture)

1. Підготувати RX-плату (`csi_recv`):

```bash
cd csi_capturing_example
./scripts/run_rx_csi_node.sh
```

Повторні запуски:

```bash
cd csi_capturing_example
./scripts/run_rx_csi_node.sh --skip-build --skip-flash
```

2. Перевірити, що серійний пристрій доступний і є потік CSI:

```bash
cd csi_capturing_example
./tools/exp --list-devices
./tools/exp --list-target-profiles
./tools/exp capture --experiment static_sign_v1 --target-profile esp32s3_csi_v1 --dry-run-packets 5 --dry-run-timeout 10s
```

Для macOS зазвичай порти мають вигляд `/dev/cu.usbmodem*` або `/dev/tty.usbmodem*`, наприклад:

```bash
./tools/exp capture --experiment static_sign_v1 --target-profile esp32s3_csi_v1 --dry-run-packets 5 --dry-run-timeout 10s --device /dev/cu.usbmodem1101
```

3. Зібрати датасет (інтерактивно `baseline` → `hands_up`):

```bash
cd csi_capturing_example
./scripts/run_static_sign_protocol.sh \
  --target-profile esp32s3_csi_v1 \
  --dataset-id 20260302_subject01_labA \
  --runs 5 \
  --duration 20s \
  --subject-id subject01 \
  --environment-id labA \
  --notes "back-to-rx posture, fixed feet marker"
```

4. Навчити та оцінити модель:

```bash
cd csi_capturing_example
./scripts/run_static_sign_train_eval.sh \
  --dataset-id 20260302_subject01_labA \
  --model svm_linear \
  --window 1s \
  --overlap 0.5
```

---

## 5) Що відбувається всередині pipeline

1. **Capture**: сирі CSI-пакети зчитуються через вибраний serial-порт (`/dev/esp32_csi`, або auto-detect, або явно заданий `--device`).
2. **Dataset layout**: створюється структура `data/experiments/static_sign_v1/<dataset_id>/<label>/run_<run_id>/`.
3. **Metadata**: для кожного run зберігаються `metadata.json` та `frames.jsonl`.
4. **Feature extraction**: віконні ознаки амплітуди CSI (mean, variance, RMS, entropy).
5. **Train**: тренується базова модель (`svm_linear` або `logreg`).
6. **Eval**: обчислюються метрики `accuracy`, `precision`, `recall`, `F1`, матриця помилок і підсумок по run.

---

## 6) Вихідні артефакти

- **Датасет**:
  - `data/experiments/static_sign_v1/<dataset_id>/...`
- **Модель**:
  - `artifacts/static_sign_v1/<dataset_id>/*.pkl`
- **Train metrics**:
  - поряд із моделлю `*.metrics.json`
- **Eval report**:
  - `out/static_sign_v1/<dataset_id>/eval_report.json`

---

## 7) Практичні поради для стабільності якості

- Балансуй дані: однакова кількість run для `baseline` і `hands_up`.
- Тримай фіксовану позу й орієнтацію людини (спиною до RX).
- Мінімізуй сторонні рухи людей під час запису.
- При зміні умов кімнати/розстановки створюй новий `dataset_id`.
