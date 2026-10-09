// Two processes alternate on a token pipe; each does `work_us` of work per turn.
// Child = compute (sqrt loop, comm "pp_cpu"); parent = memory (random 64B accesses over 1 GB, comm "pp_mem").
// Pin both to one CPU to create sub-millisecond run intervals of very different power.
// Usage: pingpong <work_us> <seconds>
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <math.h>
#include <time.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <sys/wait.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(int argc,char**argv){
 double wus=atof(argv[1])*1e-6, secs=atof(argv[2]); int a[2],b[2]; pipe(a); pipe(b); char tok=1;
 size_t sz=1ull<<30; unsigned char*buf=NULL; volatile double x=0; volatile unsigned char sink=0;
 double t0=now(); long turns=0;
 if(fork()==0){prctl(PR_SET_NAME,"pp_cpu");
  while(read(a[0],&tok,1)==1 && tok){double s=now();while(now()-s<wus)for(int i=0;i<200;i++)x+=sqrt((double)i);turns++;write(b[1],&tok,1);}
  printf("cpu_turns %ld\n",turns);return 0;}
 prctl(PR_SET_NAME,"pp_mem"); buf=malloc(sz); for(size_t i=0;i<sz;i+=4096)buf[i]=1; uint64_t seed=12345;
 write(a[1],&tok,1);
 while(now()-t0<secs){read(b[0],&tok,1);double s=now();while(now()-s<wus)for(int i=0;i<16;i++){seed=seed*6364136223846793005ULL+1;sink^=buf[seed&(sz-1)&~63ull];buf[(seed>>20)&(sz-1)&~63ull]++;}turns++;write(a[1],&tok,1);}
 read(b[0],&tok,1); tok=0; write(a[1],&tok,1); wait(NULL); printf("mem_turns %ld\n",turns); return 0;}
