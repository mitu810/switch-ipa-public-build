// Diagnostic synchronized-step mode: exercise the production pure controller.
#include "nvn_fstep.h"
#include <assert.h>
#include <stdio.h>
static unsigned checks;
static void check(int ok) { checks++; if (!ok) fprintf(stderr, "Failed synchronized-step check %u\n", checks); assert(ok); }
int main(void) {
    nvn_fstep s = {.no_catchup = 1, .ramp = 1};
    double real = 0, game = 0;
    for (int i = 0; i < 600; i++) {
        int k = s.k ? s.k : 1;
        real += k; game += k;
        assert(nvn_fstep_next(&s, k, .7, 1, 3, 0, 0) == s.k);
    }
    check(s.k == 1 && s.paid == 0 && game == real);
    for (int i = 0; i < 6; i++) {
        int k = s.k ? s.k : 1;
        double shown = k > 1.6 ? k : 1.6;
        real += shown; game += k;
        assert(nvn_fstep_next(&s, shown, 1.6, 1, 3, 0, 0) == s.k);
    }
    check(s.k == 2 && s.paid == 0 && s.debt == 0);
    check(game < real); // this diagnostic loses hitch time instead of hiding it
    for (int i = 0; i < 600; i++) {
        int k = s.k;
        assert(nvn_fstep_next(&s, k, .7, 1, 3, 0, 1) == s.k);
    }
    check(s.k == 1 && s.downs > 0 && s.paid == 0);
    check(nvn_fstep_next(&s, 3, .8, 1, 3, 3, 0) == 3 && s.k == 3);
    for (int i = 0; i < 120; i++) assert(nvn_fstep_next(&s, 2, .7, 2, 2, 0, 0) == 2);
    check(s.k == 2 && s.debt == 0);
    for (int i = 0; i < 1200; i++) {
        double shown = s.k > 1.6 ? s.k : 1.6;
        assert(nvn_fstep_next(&s, shown, 1.6, 1, 3, 0, 0) == s.k);
    }
    check(s.k == 2 && s.paid == 0);
    nvn_fstep old = {0};
    nvn_fstep_next(&old, 1, .5, 1, 3, 0, 0);
    check(nvn_fstep_next(&old, 6, .5, 1, 3, 0, 0) == 2 && old.paid == 1);
    printf("Synchronized-step diagnostic: %u checks passed; default catch-up retained\n", checks);
}
