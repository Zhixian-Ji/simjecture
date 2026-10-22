// Optional raw Thomas-Fermi adapter for the material-EOS builder.
// Linked separately against unmodified GPL-3.0-or-later FEOS sources.
#include "LIB-02_TF_TABLE.H"
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <string>

namespace {
std::mutex gate;
bool active_operation = false;
bool registered = false;
constexpr double kelvin_to_ev = 8.617333262145179e-5;
void premature_exit() { if (active_operation) std::_Exit(3); }
}

extern "C" {
void* material_tf_create(const char* filename, double mass, double z) {
    if (!filename || !std::ifstream(filename).good() || !std::isfinite(mass) ||
        mass <= 0 || !std::isfinite(z) || z < 1 || z > 118) return nullptr;
    std::lock_guard<std::mutex> lock(gate);
    if (!registered) { std::atexit(premature_exit); registered = true; }
    active_operation = true;
    std::string path(filename);
    auto* q = new QIPscheme(path.data(), mass, z, 0);
    q->interested_in_Q = 0;
    active_operation = false;
    return q;
}

int material_tf_evaluate(void* handle, double rho, double t, double* out) {
    if (!handle || !out || !std::isfinite(rho) || rho <= 0 ||
        !std::isfinite(t) || t < 0) return 1;
    std::lock_guard<std::mutex> lock(gate);
    active_operation = true;
    auto* q = static_cast<QIPscheme*>(handle);
    double p,e,s,f,z;
    q->interested_in_Q = (t > 0);
    q->getTFquantities(rho/1000., t*kelvin_to_ev, &p,&e,&s,&f,&z);
    q->interested_in_Q = 0;
    active_operation = false;
    out[0]=p*.1; out[1]=e*1e-4; out[2]=f*1e-4;
    out[3]=s*1e-4*kelvin_to_ev; out[4]=z;
    for (int i=0;i<5;i++) if (!std::isfinite(out[i])) return 2;
    return 0;
}

void material_tf_destroy(void* handle) {
    if (!handle) return;
    std::lock_guard<std::mutex> lock(gate);
    active_operation = true;
    auto* q=static_cast<QIPscheme*>(handle);
    q->ht.kill(); delete q;
    active_operation = false;
}
}
