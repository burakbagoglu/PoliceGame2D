/*
 * Piezo Hit Detector - Arduino Kodu
 * 
 * Piezo sensörden vuruş algılar ve Serial üzerinden "HIT\n" gönderir.
 * Debounce/refractory period ile spam engeller.
 * 
 * Serial komutlarla uzaktan ayar yapılabilir:
 *   T:150  → threshold = 150
 *   R:300  → refractory = 300ms
 * 
 * Bağlantı:
 *   - Piezo (+) → A0
 *   - Piezo (-) → GND
 *   - 1M ohm direnç A0 ve GND arasına (paralel)
 */

const int PIEZO_PIN = A0;                   // Piezo sensör pini

// Uzaktan ayarlanabilir parametreler (const değil!)
int hitThreshold = 100;                     // Algılama eşiği (0-1023)
unsigned long refractoryMs = 200;           // Hit sonrası bekleme süresi (ms)

unsigned long lastHitTime = 0;

// Serial komut buffer
String cmdBuffer = "";

void setup() {
  Serial.begin(9600);
  
  // Başlangıç mesajı
  Serial.println("READY");
}

void loop() {
  // === 1) Serial komutları kontrol et ===
  while (Serial.available() > 0) {
    char c = Serial.read();
    
    if (c == '\n' || c == '\r') {
      if (cmdBuffer.length() > 0) {
        processCommand(cmdBuffer);
        cmdBuffer = "";
      }
    } else {
      cmdBuffer += c;
      
      // Buffer overflow koruması
      if (cmdBuffer.length() > 20) {
        cmdBuffer = "";
      }
    }
  }
  
  // === 2) Piezo değerini oku ===
  int piezoValue = analogRead(PIEZO_PIN);
  
  // Eşik kontrolü
  if (piezoValue > hitThreshold) {
    unsigned long currentTime = millis();
    
    // Refractory period kontrolü (debounce)
    if (currentTime - lastHitTime > refractoryMs) {
      // HIT gönder
      Serial.println("HIT");
      lastHitTime = currentTime;
    }
  }
  
  // Küçük gecikme (CPU kullanımını azaltmak için)
  delay(1);
}

/**
 * Serial komutları işle
 * Format: "T:150" veya "R:300"
 */
void processCommand(String cmd) {
  cmd.trim();
  
  if (cmd.startsWith("T:")) {
    // Threshold ayarla
    int val = cmd.substring(2).toInt();
    if (val >= 0 && val <= 1023) {
      hitThreshold = val;
      Serial.print("OK:T=");
      Serial.println(hitThreshold);
    } else {
      Serial.println("ERR:T_RANGE");
    }
  }
  else if (cmd.startsWith("R:")) {
    // Refractory ayarla
    int val = cmd.substring(2).toInt();
    if (val >= 50 && val <= 5000) {
      refractoryMs = (unsigned long)val;
      Serial.print("OK:R=");
      Serial.println(refractoryMs);
    } else {
      Serial.println("ERR:R_RANGE");
    }
  }
  else if (cmd == "STATUS") {
    // Mevcut değerleri bildir
    Serial.print("STATUS:T=");
    Serial.print(hitThreshold);
    Serial.print(",R=");
    Serial.println(refractoryMs);
  }
}
