#include <Servo.h>
#include <stdlib.h>
#include <string.h>

const uint8_t SERVO_PINS[3] = {7, 9, 11};
const int PULSE_MIN_US = 544;
const int PULSE_MAX_US = 2400;
Servo servos[3];
char inputBuffer[32];
uint8_t inputLength = 0;
bool inputOverflow = false;

// Tenths of a degree. Preserve the original integer-angle pulse mapping.
void writeAngles(const int tenths[3]) {
  for (uint8_t i = 0; i < 3; i++) {
    const int pulse = PULSE_MIN_US +
        (long)tenths[i] * (PULSE_MAX_US - PULSE_MIN_US) / 1800;
    servos[i].writeMicroseconds(pulse);
  }
}

bool parseAngle(char *&cursor, int &tenths) {
  if (*cursor < '0' || *cursor > '9') return false;
  char *end;
  long whole = strtol(cursor, &end, 10);
  if (whole < 0 || whole > 180) return false;
  cursor = end;
  tenths = whole * 10;
  if (*cursor == '.') {
    cursor++;
    if (*cursor < '0' || *cursor > '9') return false;
    tenths += *cursor++ - '0';
  }
  return tenths <= 1800;
}

void printAngle(int tenths) {
  Serial.print(tenths / 10);
  if (tenths % 10) {
    Serial.print('.');
    Serial.print(tenths % 10);
  }
}

void handleCommand(char *command) {
  if (strcmp(command, "CAPS") == 0) {
    Serial.println("CAPS fractional-0.1 pulse-us");
    return;
  }
  int tenths[3];
  char *cursor = command;
  for (uint8_t i = 0; i < 3; i++) {
    if (!parseAngle(cursor, tenths[i]) ||
        (i < 2 ? *cursor != ',' : *cursor != '\0')) {
      Serial.println("ERR expected: 0..180 angles in 0.1 degree steps");
      return;
    }
    if (i < 2) cursor++;
  }
  writeAngles(tenths);
  Serial.print("OK ");
  for (uint8_t i = 0; i < 3; i++) {
    if (i) Serial.print(',');
    printAngle(tenths[i]);
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < 3; i++) {
    servos[i].attach(SERVO_PINS[i], PULSE_MIN_US, PULSE_MAX_US);
  }
  const int initial[3] = {900, 900, 900};
  writeAngles(initial);
  Serial.println("READY");
}

void loop() {
  while (Serial.available() > 0) {
    const char incoming = Serial.read();
    if (incoming == '\n') {
      inputBuffer[inputLength] = '\0';
      if (inputOverflow) Serial.println("ERR command too long");
      else handleCommand(inputBuffer);
      inputLength = 0;
      inputOverflow = false;
    } else if (incoming != '\r') {
      if (inputLength < sizeof(inputBuffer) - 1) inputBuffer[inputLength++] = incoming;
      else inputOverflow = true;
    }
  }
}
