/*
 * Piezo Hit Detector - Arduino Kodu
 * 
 * Piezo sensörden vuruş algılar ve Serial üzerinden "HIT\n" gönderir.
 * Debounce/refractory period ile spam engeller.
 * 
 * Bağlantı:
 *   - Piezo (+) → A0
 *   - Piezo (-) → GND
 *   - 1M ohm direnç A0 ve GND arasına (paralel)
 */

const int PIEZO_PIN = A0;           // Piezo sensör pini
const int THRESHOLD = 100;          // Algılama eşiği (0-1023)
const unsigned long REFRACTORY_MS = 200;  // Hit sonrası bekleme süresi (ms)

unsigned long lastHitTime = 0;

void setup() {
  Serial.begin(9600);
  
  // Başlangıç mesajı
  Serial.println("READY");
}

void loop() {
  // Piezo değerini oku
  int piezoValue = analogRead(PIEZO_PIN);
  
  // Eşik kontrolü
  if (piezoValue > THRESHOLD) {
    unsigned long currentTime = millis();
    
    // Refractory period kontrolü (debounce)
    if (currentTime - lastHitTime > REFRACTORY_MS) {
      // HIT gönder
      Serial.println("HIT");
      lastHitTime = currentTime;
    }
  }
  
  // Küçük gecikme (CPU kullanımını azaltmak için)
  delay(1);
}
