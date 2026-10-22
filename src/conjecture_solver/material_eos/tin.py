"""Tin EOS reconstruction. All public inputs and outputs use SI units.

The ionic formulas are Eqs. (7), (16)--(21), (41)--(42) of Ding et al.
The compressed branch follows Eqs. (35)--(38). The expanded branch offers:
  printed: the paper's literal additive soft-sphere coefficients (audit only);
  calibrated: a FEOS-style replacement of the TF cold curve, recalibrated
              to the paper's P0, E0, B0, Ecoh, and attractive exponent m.
The latter is an explicit reconstruction choice, not a claimed author correction.
Neither branch includes a Maxwell construction or multiphase equilibrium.
"""

import math
from dataclasses import asdict, dataclass
from functools import lru_cache

import numpy as np
from scipy.constants import Boltzmann, atomic_mass
from scipy.optimize import brentq

from .backend import ThomasFermi


@dataclass(frozen=True)
class Parameters:
    atomic_weight: float = 118.71
    z: int = 50
    rho0: float = 7550.0
    t0: float = 300.0
    bulk0: float = 58e9
    theta0: float = 160.0
    gamma_ref: float = 2.283
    gamma_prime_left: float = -1.58
    gamma_prime_right: float = -1.58
    gamma_infinity: float = 2 / 3
    gamma_dilute: float = 1.0
    tm0: float = 505.0
    alpha: float = 1 / 3
    cohesive_energy: float = 2.55e6  # 2.55e10 erg/g
    soft_m: float = 0.5817
    soft_n_printed: float = 2.7714
    soft_a_cgs_printed: float = 2.499e7
    soft_b_cgs_printed: float = 9.958e9

    @property
    def gas_constant(self):
        return Boltzmann / (self.atomic_weight * atomic_mass)


@dataclass(frozen=True)
class State:
    pressure: float
    energy: float
    free_energy: float
    entropy: float

    def as_dict(self):
        return asdict(self)


_x, _w = np.polynomial.legendre.leggauss(64)
_nodes, _weights = (_x + 1) / 2, _w / 2


def debye3(u):
    """Debye D_3: stable small-u series, Gaussian quadrature, large-u limit."""
    if u <= 0:
        raise ValueError("Debye argument must be positive")
    if u < 0.03:
        return 1 - 3 * u / 8 + u * u / 20 - u**4 / 1680 + u**6 / 90720
    if u > 50:
        return math.pi**4 / (5 * u**3)
    return float(3 * u * np.dot(_weights, _nodes**3 / np.expm1(u * _nodes)))


class Ionic:
    def __init__(self, params=None):
        self.params = params or Parameters()
        self.vibration = lru_cache(maxsize=32768)(self._vibration)

    def _vibration(self, rho):
        """theta_D, T_switch, Gamma, dGamma/dln(rho)."""
        if not math.isfinite(rho) or rho <= 0:
            raise ValueError("Density must be finite and positive")
        p = self.params
        eta = rho / p.rho0
        if eta >= 1:
            a = 2 * p.gamma_ref - 2 * p.gamma_infinity + p.gamma_prime_right
            b = p.gamma_infinity - p.gamma_ref - p.gamma_prime_right
            gamma = p.gamma_infinity + a / eta + b / eta**2
            gp = -a / eta - 2 * b / eta**2
            theta = (
                p.theta0
                * eta**p.gamma_infinity
                * math.exp(a * (1 - 1 / eta) + b / 2 * (1 - 1 / eta**2))
            )
        else:
            c = 2 * p.gamma_ref - 2 * p.gamma_dilute - p.gamma_prime_left
            d = p.gamma_dilute - p.gamma_ref + p.gamma_prime_left
            gamma = p.gamma_dilute + c * eta + d * eta**2
            gp = c * eta + 2 * d * eta**2
            theta = p.theta0 * eta**p.gamma_dilute * math.exp(c * (eta - 1) + d / 2 * (eta**2 - 1))
        tm = p.tm0 * (theta / p.theta0) ** 2 * eta ** (-2 / 3)
        return theta, tm, gamma, gp

    def evaluate(self, rho, temperature, branch=None):
        """Return state, Cv, isothermal B, and dP/dT; derivatives are analytic."""
        if not math.isfinite(temperature) or temperature <= 0:
            raise ValueError("Temperature must be finite and positive")
        theta, tm, gamma, gp = self.vibration(rho)
        u, w = theta / temperature, tm / temperature
        r = self.params.gas_constant
        solid = w > 1 if branch is None else branch == "solid"
        if branch not in (None, "solid", "liquid"):
            raise ValueError("Unknown ionic branch")
        if solid:
            d = debye3(u)
            f = 9 * u / 8 + 3 * math.log(-math.expm1(-u)) - d
            e = 9 * u / 8 + 3 * d
            c = 12 * d - (9 * u / math.expm1(u) if u < 700 else 0.0)
            h = gamma * e
            bulk = rho * r * temperature * ((gamma + gp) * e + gamma**2 * (e - c))
            dpdt = rho * r * gamma * c
        else:
            a = self.params.alpha
            wa = w**a
            # Eq. (16); use logarithms separately to avoid small-u underflow.
            f = (
                1.5 * (2 * math.log(u) - math.log(w))
                - 1
                + 1.5 * wa / (a * (1 - a))
                - 1.5 * a * w / (1 - a)
                - 1.5 * (1 + 1 / a)
            )
            e = 1.5 + 1.5 * wa / (1 - a) - 1.5 * a * w / (1 - a)
            c = 1.5 * (1 + wa)
            gm = 2 * gamma - 2 / 3
            h = 3 * gamma + gm * (e - 3)
            bulk = rho * r * temperature * (h + 3 * gp + 2 * gp * (e - 3) + gm**2 * (e - c))
            dpdt = rho * r * (h - gm * (e - c))
        state = State(
            rho * r * temperature * h, r * temperature * e, r * temperature * f, r * (e - f)
        )
        return state, r * c, bulk, dpdt


class TinEOS:
    def __init__(self, mode="calibrated", params=None, electrons=None):
        params = params or Parameters()
        if mode not in ("calibrated", "printed"):
            raise ValueError("mode must be calibrated or printed")
        self.mode, self.params = mode, params
        self.ions = Ionic(params)
        self.electrons = electrons or ThomasFermi(params.atomic_weight, params.z)
        p = params
        self.e0 = self.electrons.evaluate(p.rho0, p.t0)
        self.ec0 = self.electrons.evaluate(p.rho0, 0.0)
        self.i0, _, bi0, _ = self.ions.evaluate(p.rho0, p.t0)
        be0 = self.electrons.bulk_modulus(p.rho0, p.t0)
        # Match total pressure and bulk modulus at the reference point.
        pbase = self.e0[0] + self.i0.pressure
        self.morse_b = 3 * (be0 + bi0 - p.bulk0) / pbase - 2
        if abs(self.morse_b) < 1e-12:
            raise ValueError("Degenerate Morse calibration")
        self.morse_energy = 3 * pbase / (p.rho0 * self.morse_b)
        # Electron energy has already been shifted by E_e(rho0,T0).
        self.morse_offset = -self.i0.energy
        # In FEOS the low-density soft sphere replaces the TF cold curve.
        # Refit n, A, B with m and Ecoh fixed, satisfying P0, E0, B0.
        bec0 = self.electrons.bulk_modulus(p.rho0, 0.0)
        self.soft_e0 = -self.i0.energy - (self.e0[1] - self.ec0[1])
        self.soft_p0 = -self.i0.pressure - (self.e0[0] - self.ec0[0])
        k = (p.bulk0 - bi0 - (be0 - bec0)) / p.rho0
        q = self.soft_p0 / p.rho0
        d = self.soft_e0 - p.cohesive_energy
        m = p.soft_m
        self.soft_n = (k - (m + 1) * q) / (q - m * d)
        self.soft_x = (q - m * d) / (self.soft_n - m)  # A*rho0^n, SI J/kg
        self.soft_y = self.soft_x - d  # B*rho0^m
        if not (self.soft_n > m > 0 and self.soft_x > 0 and self.soft_y > 0):
            raise ValueError("Unphysical calibrated soft-sphere coefficients")
        self.calibration = {
            "mode": mode,
            "parameters_SI": asdict(params),
            "TF_reference": dict(
                zip(["P_Pa", "E_J_kg", "F_J_kg", "S_J_kg_K"], self.e0, strict=True)
            ),
            "morse_b": self.morse_b,
            "morse_energy_J_kg": self.morse_energy,
            "morse_offset_after_electron_shift_J_kg": self.morse_offset,
            "soft_n_calibrated": self.soft_n,
            "soft_m": m,
            "soft_A_rho0_to_n_J_kg": self.soft_x,
            "soft_B_rho0_to_m_J_kg": self.soft_y,
            "soft_A_cgs_calibrated": self.soft_x * 1e4 / (p.rho0 / 1000) ** self.soft_n,
            "soft_B_cgs_calibrated": self.soft_y * 1e4 / (p.rho0 / 1000) ** m,
            "soft_Ecoh_J_kg": p.cohesive_energy,
            "TF_reference_bulk_Pa": be0,
        }

    def soft_sphere(self, rho, printed=False):
        p = self.params
        if printed:
            r = rho / 1000
            n = p.soft_n_printed
            x = p.soft_a_cgs_printed * r**n * 1e-4
            y = p.soft_b_cgs_printed * r**p.soft_m * 1e-4
        else:
            n = self.soft_n
            eta = rho / p.rho0
            x, y = self.soft_x * eta**n, self.soft_y * eta**p.soft_m
        return (
            x - y + p.cohesive_energy,
            rho * (n * x - p.soft_m * y),
            rho * (n * (n + 1) * x - p.soft_m * (p.soft_m + 1) * y),
        )

    def evaluate(self, rho, temperature, density_branch=None):
        """Evaluate total EOS. density_branch is reserved for one-sided audits."""
        p = self.params
        ion = self.ions.evaluate(rho, temperature)[0]
        pe, ee, fe, se = self.electrons.evaluate(float(rho), float(temperature))
        low = rho < p.rho0 if density_branch is None else density_branch == "low"
        if density_branch not in (None, "low", "high"):
            raise ValueError("Unknown density branch")
        if low and self.mode == "calibrated":
            pc, ec, _, _ = self.electrons.evaluate(float(rho), 0.0)
            fc, pb, _ = self.soft_sphere(rho)
            return State(
                ion.pressure + pe - pc + pb,
                ion.energy + ee - ec + fc,
                ion.free_energy + fe - ec + fc,
                ion.entropy + se,
            )
        if low:
            fc, pb, _ = self.soft_sphere(rho, printed=True)
        else:
            eta = rho / p.rho0
            exponent = self.morse_b * (1 - eta ** (-1 / 3))
            fc = -self.morse_energy * math.expm1(exponent)
            pb = (
                -self.morse_energy * self.morse_b * p.rho0 / 3 * eta ** (2 / 3) * math.exp(exponent)
            )
            if self.mode == "printed":
                # Eq. (36): common printed bonding free-energy reference.
                fc += self.soft_sphere(p.rho0, printed=True)[0]
            else:
                fc += self.morse_offset
        # A constant electron energy shift is permitted by its reference freedom.
        # For printed mode choose it to make E=0 on both sides at rho0; this
        # cannot fix the pressure or bulk-modulus discrepancy.
        shift = self.e0[1]
        if self.mode == "printed":
            shift += self.i0.energy + self.soft_sphere(p.rho0, printed=True)[0]
        return State(
            ion.pressure + pe + pb,
            ion.energy + ee - shift + fc,
            ion.free_energy + fe - shift + fc,
            ion.entropy + se,
        )

    def derivatives(self, rho, temperature, h=1e-3):
        """Cv, isothermal bulk modulus, dP/dT, cs^2.

        Central stencils are invalid across either piecewise boundary.
        Return an explicit flag rather than hiding such stencils.
        """
        states_t = [self.evaluate(rho, temperature * (1 + i * h)) for i in (-2, -1, 1, 2)]
        cv = (
            states_t[0].energy
            - 8 * states_t[1].energy
            + 8 * states_t[2].energy
            - states_t[3].energy
        ) / (12 * h * temperature)
        dpdt = (
            states_t[0].pressure
            - 8 * states_t[1].pressure
            + 8 * states_t[2].pressure
            - states_t[3].pressure
        ) / (12 * h * temperature)
        states_r = [self.evaluate(rho * (1 + i * h), temperature) for i in (-2, -1, 1, 2)]
        bulk = (
            states_r[0].pressure
            - 8 * states_r[1].pressure
            + 8 * states_r[2].pressure
            - states_r[3].pressure
        ) / (12 * h)
        cs2 = bulk / rho + temperature * dpdt**2 / (rho * rho * cv) if cv != 0 else math.nan
        tm = self.ions.vibration(rho)[1]
        r_branches = [
            temperature < self.ions.vibration(rho * (1 + i * h))[1] for i in (-2, -1, 1, 2)
        ]
        crosses = (
            temperature * (1 - 2 * h) <= tm <= temperature * (1 + 2 * h) or len(set(r_branches)) > 1
        )
        return {
            "cv_J_kg_K": cv,
            "bulk_Pa": bulk,
            "dP_dT_Pa_K": dpdt,
            "sound_speed_squared_m2_s2": cs2,
            "stencil_crosses_ionic_switch": crosses,
        }

    def hugoniot(self, rho):
        """Solve the Rankine-Hugoniot equation without bracketing across jumps."""
        p = self.params
        if rho <= p.rho0:
            raise ValueError("Shock density must exceed reference density")
        initial = self.evaluate(p.rho0, p.t0)
        dv = 1 / p.rho0 - 1 / rho

        def residual(t):
            s = self.evaluate(rho, t)
            return s.energy - initial.energy - 0.5 * (s.pressure + initial.pressure) * dv

        tm = self.ions.vibration(rho)[1]
        # Separate continuous intervals on either side of the ionic switch.
        bounds = [(p.t0, tm * (1 - 1e-10)), (max(p.t0, tm), 1e8)]
        roots = []
        for lo, hi in bounds:
            if hi <= lo:
                continue
            grid = np.geomspace(lo, hi, 75)
            left, fl = grid[0], residual(grid[0])
            for right in grid[1:]:
                fr = residual(right)
                if fl * fr <= 0:
                    t = brentq(residual, left, right, xtol=1e-6, rtol=1e-11)
                    if abs(residual(t)) < max(0.1, abs(self.evaluate(rho, t).energy) * 1e-7):
                        roots.append(t)
                left, fl = right, fr
        if not roots:
            raise RuntimeError(f"No continuous Hugoniot root at rho={rho}")
        t = min(roots)
        state = self.evaluate(rho, t)
        up = math.sqrt((state.pressure - initial.pressure) * dv)
        us = up / (p.rho0 * dv)
        return {
            "rho_kg_m3": rho,
            "T_K": t,
            "P_Pa": state.pressure,
            "E_J_kg": state.energy,
            "up_m_s": up,
            "Us_m_s": us,
            "RH_residual_J_kg": residual(t),
            "root_count": len(roots),
        }
