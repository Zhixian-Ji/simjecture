// Simjecture adapter for the separately installed GPL-3.0-or-later FEOS library.
// Uses Get_EOS_All: FEOS 16.7's task-selecting Get_EOS has switch fall-through.
#include "libfeos.h"
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
bool finished = false;
void incomplete_exit() {
    // Upstream sometimes calls exit(0) on an error. Never advertise that as success.
    if (!finished) {
        std::cerr << "FEOS terminated before the adapter completed its output\n";
        std::_Exit(3);
    }
}
double number(const std::string &text) {
    size_t end;
    double value = std::stod(text, &end);
    if (end != text.size() || !std::isfinite(value) || value <= 0)
        throw std::runtime_error("Density and temperature must be finite and positive");
    return value;
}
const char *names[] = {"pressure", "energy", "entropy", "free_energy", "zbar",
    "electron_pressure", "electron_energy", "electron_entropy", "electron_free_energy",
    "ion_pressure", "ion_energy", "ion_entropy", "ion_free_energy",
    "tf_pressure", "tf_energy", "tf_entropy", "tf_free_energy"};
struct State {
    double value[17];
    int below = -1;
};
State evaluate(int elements, double molecules, double rho, double temperature) {
    State state;
    for (double &v : state.value) v = std::numeric_limits<double>::quiet_NaN();
    std::vector<double> charge(elements, std::numeric_limits<double>::quiet_NaN());
    auto &v = state.value;
    FEOS_Get_EOS_All(1, 0, rho, temperature, &state.below,
        &v[0], &v[1], &v[2], &v[3], charge.data(), &v[4],
        &v[5], &v[6], &v[7], &v[8], &v[9], &v[10], &v[11], &v[12],
        &v[13], &v[14], &v[15], &v[16]);
    v[4] /= molecules;
    for (double x : v)
        if (!std::isfinite(x)) throw std::runtime_error("Nonfinite FEOS result");
    for (double x : charge)
        if (!std::isfinite(x)) throw std::runtime_error("Nonfinite FEOS charge state");
    if (state.below != 0 || v[4] < 0)
        throw std::runtime_error("Invalid FEOS phase flag or charge state");
    return state;
}
}

int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::cout << "FEOS 16.7 / Simjecture query adapter 1\n";
        return 0;
    }
    try {
        bool batch = argc == 5 && std::string(argv[1]) == "--batch";
        if ((!batch && argc != 5) || (argc > 1 && std::string(argv[1]) == "--help")) {
            std::cerr << "usage: feos-query MATERIAL_ID RHO_G_CM3 T_EV OUTPUT.json\n"
                      << "   or: feos-query --batch MATERIAL_ID STATES.csv OUTPUT.csv\n"
                      << "Working directory requires FEOS_Material-DB.dat and FEOS_TF-Table_1197.dat.\n"
                      << "STATES.csv: header rho_g_cm3,temperature_eV then two positive numbers per row.\n"
                      << "Homogeneous EOS only: Maxwell construction and soft-sphere replacement disabled.\n";
            return 2;
        }
        int material = std::stoi(argv[batch ? 2 : 1]);
        if (material < 1000 || material > 9999) throw std::runtime_error("Invalid material ID");
        std::ifstream database("FEOS_Material-DB.dat"), tf("FEOS_TF-Table_1197.dat");
        if (!database || !tf) throw std::runtime_error("FEOS database or TF table is missing");
        std::string contents((std::istreambuf_iterator<char>(database)), {});
        if (contents.find("Material-" + std::to_string(material) + ":") == std::string::npos)
            throw std::runtime_error("Material ID is absent from the supplied database");
        std::vector<std::pair<double, double>> states;
        if (batch) {
            std::ifstream input(argv[3]);
            if (!input) throw std::runtime_error("Cannot open states CSV");
            std::string line;
            if (!std::getline(input, line) || line != "rho_g_cm3,temperature_eV")
                throw std::runtime_error("Unexpected states CSV header");
            while (std::getline(input, line)) {
                auto comma = line.find(',');
                if (comma == std::string::npos) throw std::runtime_error("Malformed state row");
                states.emplace_back(number(line.substr(0, comma)), number(line.substr(comma + 1)));
            }
            if (states.empty()) throw std::runtime_error("Empty states CSV");
        } else {
            states.emplace_back(number(argv[2]), number(argv[3]));
        }
        std::atexit(incomplete_exit);
        FEOS_Initialize(1, 0);
        int elements = 0, maxwell_count = 0;
        double reliable = std::numeric_limits<double>::quiet_NaN();
        FEOS_Init_Mat(1, material, 0, 0, nullptr, 0, &elements, &maxwell_count, &reliable);
        if (elements < 1 || elements > 100) throw std::runtime_error("Invalid element count");
        std::vector<double> a(elements), z(elements), x(elements);
        double atot, ztot, xtot, tref, rhoref, bulk, eoffset, ioffset;
        int sesame;
        FEOS_Get_Mat_Par(1, a.data(), z.data(), x.data(), &atot, &ztot, &xtot,
                         &tref, &rhoref, &bulk, &sesame);
        FEOS_Get_Energy_Offsets(1, &eoffset, &ioffset);
        if (!std::isfinite(xtot) || xtot <= 0) throw std::runtime_error("Invalid composition");
        std::ofstream output(argv[4]);
        if (!output) throw std::runtime_error("Cannot open output");
        output << std::setprecision(17);
        if (batch) {
            output << "rho_g_cm3,temperature_eV";
            for (auto name : names) output << ',' << name;
            output << '\n';
        }
        for (auto [rho, temperature] : states) {
            State result = evaluate(elements, xtot, rho, temperature);
            if (result.value[4] > ztot / xtot * (1 + 1e-10))
                throw std::runtime_error("Charge state exceeds the atomic number");
            if (batch) {
                output << rho << ',' << temperature;
                for (double v : result.value) output << ',' << v;
                output << '\n';
            } else {
                output << "{\"package\":\"FEOS\",\"version\":\"16.7\",\"material_id\":" << material
                    << ",\"model\":\"QEOS: Thomas-Fermi + Cowan + bonding\","
                    << "\"units\":{\"density\":\"g/cm3\",\"temperature\":\"eV\","
                    << "\"pressure\":\"dyn/cm2\",\"energy\":\"erg/g\",\"entropy\":\"erg/(g eV)\"},"
                    << "\"density\":" << rho << ",\"temperature\":" << temperature
                    << ",\"reference_density\":" << rhoref << ",\"reference_temperature\":" << tref
                    << ",\"reference_bulk_modulus\":" << bulk
                    << ",\"atomic_mass\":" << atot / xtot << ",\"atomic_number\":" << ztot / xtot
                    << ",\"electron_energy_offset\":" << eoffset << ",\"ion_energy_offset\":" << ioffset
                    << ",\"maxwell\":false,\"soft_sphere\":false";
                for (int i = 0; i < 17; ++i) output << ",\"" << names[i] << "\":" << result.value[i];
                output << "}\n";
            }
        }
        output.flush();
        if (!output) throw std::runtime_error("Failed to write results");
        FEOS_Finalize();
        finished = true;
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "feos-query: " << error.what() << '\n';
        finished = true; // Our own explicit nonzero error is not an upstream exit(0).
        return 2;
    }
}
