import pathlib, subprocess, tempfile
root=pathlib.Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as folder:
 p=pathlib.Path(folder)
 (p/'Servo.h').write_text('''#include <cstdint>
#include <string>
#include <sstream>
struct Servo { int pulse=0; int writes=0; void attach(int,int,int){} void writeMicroseconds(int p){pulse=p;writes++;} };
struct SerialMock { std::string output; void begin(int){} int available(){return 0;} char read(){return 0;} template<class T> void print(T value){std::ostringstream s;s<<value;output+=s.str();} void println(){output+="\\n";} template<class T> void println(T value){print(value);println();} };
extern SerialMock Serial;
''')
 (p/'check.cpp').write_text('''#include "Servo.h"
#include <cassert>
SerialMock Serial;
'''+'#include "'+str(root/'arduino/servo_controller/servo_controller.ino')+'"\n'+'''
void command(const char* text){char buffer[64];strcpy(buffer,text);Serial.output.clear();handleCommand(buffer);}
int main(){
 setup();assert(servos[0].pulse==1472);
 command("CAPS");assert(Serial.output=="CAPS fractional-0.1 pulse-us\\n");assert(servos[0].writes==1);
 command("90.1,90,100.3");assert(Serial.output=="OK 90.1,90,100.3\\n");assert(servos[0].pulse==1473);assert(servos[1].pulse==1472);assert(servos[2].pulse==1578);
 command("90.2,90,100.4");assert(servos[0].pulse==1474);assert(servos[2].pulse==1579);
 const char* invalid[]={"90.15,90,100", "90,90,180.1", "90,90,90junk", "-1,90,90", "nan,90,90", "90,90", "90,,90", "999999999999999999999,90,90"};
 int before=servos[0].writes;for(auto text:invalid){command(text);assert(Serial.output.find("ERR")==0);assert(servos[0].writes==before);}
 for(int angle=0;angle<=180;angle++){char text[32];sprintf(text,"%d,90,90",angle);command(text);assert(servos[0].pulse==544+(long)angle*1856/180);}
}
''')
 # The Arduino builder inserts Servo.h once; the native harness needs a guard.
 h=p/'Servo.h';h.write_text('#pragma once\n'+h.read_text())
 subprocess.run(['g++','-std=c++11','-I',str(p),str(p/'check.cpp'),'-o',str(p/'check')],check=True)
 subprocess.run([str(p/'check')],check=True)
 print('Firmware checks passed: decimal pulses, integer compatibility, malformed commands, read-only CAPS.')
