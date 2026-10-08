#!/usr/bin/env python3
"""Seeded generator for fictional CampusLens student records.

Every record is invented. No real student, advisor or institution data is
used; the "DEM" prefix marks demonstration ids. Four fixed, fictional students lead the file
with made-up, good-standing values. The same seed and count always give the same file. Standard library only.

    python3 generate_students.py                      # 5,000 students -> students.json
    python3 generate_students.py --count 500 --seed 7 --out small.json

Record shape:
    {"studentId": "DEM1001", "name": "Avery Lindgren", "program": "Computer Science",
     "degreeProgress": 0.56, "currentGPA": 2.31, "previousGPA": 3.12,
     "holds": ["Financial Balance"], "advisor": "Dr. Smith"}
"""
import argparse
import json
import random

# program -> share of enrolment (weights, not percentages). An invented mix
# for a mid-sized private university; not any real institution's figures.
PROGRAMS = {
    "Business Administration": 12, "Nursing": 11, "Computer Science": 8,
    "Psychology": 8, "Biology": 7, "Engineering": 7, "Theology": 6,
    "Elementary Education": 5, "Communication": 5, "Health and Exercise Science": 5,
    "Finance": 4, "Marketing": 4, "Social Work": 3, "Music": 3,
    "Graphic Design": 3, "English": 2, "Mathematics": 2, "History": 2,
    "Chemistry": 2, "Undeclared": 1,
}
STUDENTS_PER_ADVISOR = 45   # a typical faculty-advisor caseload
SURNAMES = """Smith Okafor Ramirez Whitfield Chen Abernathy Patel Lindqvist Nakamura Osei
Kowalski Adeyemi Brooks Hartley Delgado Varga Thompson Mwangi Castillo Reyes Bennett
Ibrahim Sorensen Gallagher Nguyen Fitzgerald Mbeki Alvarez Carmichael Dubois Eze Farrow
Goldberg Hosseini Ingram Jovanovic Kaplan Lachance Mendoza Novak Oyelaran Pemberton
Quintero Rasmussen Sandoval Tanaka Underwood Vasquez Wainwright Yamamoto Zielinski
Ashworth Banerjee Callahan Dlamini Easton Fontaine Grigoryan Halloran Iwasaki Jorgensen
Kimathi Lombardi Marchetti Nwosu Ortega Prescott Rahman Santoro Tavares Ulloa Venter
Whitaker Xiong Yilmaz Zamora Atkinson Boateng Corrigan Desai Engstrom Ferreira Gutierrez
Haddad Ivanov Jamison Kovacs Leclerc Morales Ndlovu Okonkwo Pacheco Romano Schofield
Teague Uchida Valdez Winslow Yoder Abara Blackwood Cisneros Doyle Ekwueme Flanagan
Grabowski Hayashi Irving Jimenez Keating Lozano Maddox Nakata Oduya Pritchard""".split()


FIRST_NAMES = """Aaliyah Abel Adaeze Aiden Alejandro Amara Andre Anika Asher Beatriz Benjamin
Bianca Caleb Camila Carlos Chidi Chloe Daniel Daria Davon Diego Elena Elijah Emeka Emma
Ethan Fatima Felipe Gabriela Grace Hana Hannah Hassan Ian Imani Isaac Isabella Jamal
Jasmine Javier Jin Jonah Jordan Kai Kayla Kenji Kwame Laila Leah Liam Lucia Malik
Mariana Mateo Maya Mei Micah Miguel Naomi Nathan Nia Noah Nora Olivia Omar Paloma
Priya Rafael Rachel Ravi Rebecca Samuel Sanjay Sara Sofia Tariq Tessa Thabo Thomas
Uche Valeria Victor Wei Xavier Yara Yusuf Zainab Zoe""".split()

# Four fixed, fictional demonstration students at the front of the file, in
# good standing: no generated GPA drop or hold is ever attached to them.
TEAM = [
    ("Avery Lindgren", 0.62, 3.71, 3.64),
    ("Marcus Delacroix", 0.74, 3.68, 3.59),
    ("Priyanka Venkataraman", 0.58, 3.82, 3.80),
    ("Teodor Wilkins", 0.66, 3.57, 3.49),
]
TEAM_PROGRAM = "Computer Science"


def advisors_for(count):
    """Each program's advisors, sized to its share of `count` students.
    Every advisor has a distinct surname and belongs to one program."""
    total = sum(PROGRAMS.values())
    names = iter(SURNAMES)
    out = {}
    for program, weight in PROGRAMS.items():
        n = max(1, round(count * weight / total / STUDENTS_PER_ADVISOR))
        out[program] = ["Dr. " + next(names) for _ in range(n)]
    return out


HOLDS = ["Financial Balance", "Advising Required", "Missing Transcript",
         "Immunization Record", "Library Fine", "Housing Deposit"]


def progress(rng):
    """Share of the degree completed. Class sizes shrink each year (students
    leave or transfer), so more students sit early in their degree."""
    year = rng.choices([0, 1, 2, 3], weights=[30, 26, 23, 21])[0]
    return round(min(0.99, max(0.0, (year + rng.betavariate(2, 2)) / 4 + rng.gauss(0, 0.03))), 2)


def gpa(x):
    return round(min(4.0, max(0.0, x)), 2)


def student(rng, number, advisors, name):
    program = rng.choices(list(PROGRAMS), weights=list(PROGRAMS.values()))[0]
    # Three kinds of term: steady (most), a sharp drop, or a recovery.
    kind = rng.choices(["steady", "drop", "recover"], weights=[78, 14, 8])[0]
    previous = gpa(rng.gauss(3.05, 0.55))
    change = {"steady": rng.gauss(0.0, 0.15),
              "drop": -rng.uniform(0.5, 1.1),
              "recover": rng.uniform(0.3, 0.8)}[kind]
    current = gpa(previous + change)

    # Holds are more likely for a student whose GPA fell or is low.
    holds = []
    p_fin = 0.10 + (0.30 if kind == "drop" else 0) + (0.10 if current < 2.0 else 0)
    if rng.random() < p_fin:
        holds.append("Financial Balance")
    p_adv = 0.06 + (0.25 if current < 2.0 else 0) + (0.10 if kind == "drop" else 0)
    if rng.random() < p_adv:
        holds.append("Advising Required")
    for other in HOLDS[2:]:
        if rng.random() < 0.03:
            holds.append(other)

    return {
        "studentId": f"DEM{number}",
        "name": name,
        "program": program,
        "degreeProgress": progress(rng),
        "currentGPA": current,
        "previousGPA": previous,
        "holds": holds,
        "advisor": rng.choice(advisors[program]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--start", type=int, default=1001)
    ap.add_argument("--out", default="students.json")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    advisors = advisors_for(args.count)
    # Names come from their own random stream, so adding or changing them
    # never alters anyone's programme, GPA or holds.
    name_rng = random.Random(args.seed + 1)
    taken = {member[0] for member in TEAM}
    rows = []
    for i in range(args.count):
        while True:
            name = name_rng.choice(FIRST_NAMES) + " " + name_rng.choice(SURNAMES)
            if name not in taken:
                taken.add(name)
                break
        rows.append(student(rng, args.start + i, advisors, name))
    team_advisor = advisors[TEAM_PROGRAM][0]
    for i, (name, done, current, previous) in enumerate(TEAM[:args.count]):
        rows[i] = {"studentId": f"DEM{args.start + i}", "name": name,
                   "program": TEAM_PROGRAM, "degreeProgress": done,
                   "currentGPA": current, "previousGPA": previous,
                   "holds": [], "advisor": team_advisor}
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")
    print(f"wrote {len(rows)} students to {args.out}")


if __name__ == "__main__":
    main()
