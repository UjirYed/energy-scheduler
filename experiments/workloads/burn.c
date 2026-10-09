// Continuous CPU burner (same inner loop as ../../stress_core.c, without its 5s-on/5s-off duty cycle).
// Usage: burn <seconds>          run for a fixed time, print iterations/second (throughput)
//        burn 0 <iterations>     run a fixed amount of work, print elapsed seconds
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(int argc,char**argv){double secs=argc>1?atof(argv[1]):10;long iters=argc>2?atol(argv[2]):0;volatile double x=0;double t0=now();long it=0;
 while(iters?it<iters:now()-t0<secs){for(int i=0;i<1000000;i++)x+=sqrt((double)i);if(x>1e12)x=0;it++;}
 if(iters)printf("%.4f\n",now()-t0);else printf("%.3f\n",it/(now()-t0));return 0;}
