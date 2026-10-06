"""Static catalog for the Demonstration University generator.

Colleges, subjects, course titles, academic programs, program requirements,
and prerequisites. Everything here is fictional and hand-written for the
demonstration; nothing is copied from any real institution's catalog.
Stdlib only, no logic beyond small helpers.
"""

from __future__ import annotations

COLLEGES: list[tuple[str, str]] = [
    ("CAS", "College of Arts and Sciences"),
    ("COB", "College of Business"),
    ("CEC", "College of Engineering and Computing"),
    ("COE", "College of Education"),
    ("CNH", "College of Nursing and Health Sciences"),
    ("CTA", "College of Theology, Ministry, and the Arts"),
]

# subject code -> (name, college, hand-written courses "NNNN Title").
# The second digit of a course number is its credit hours (4690 = 6 hours).
SUBJECTS: dict[str, tuple[str, str, list[str]]] = {
    "UNIV": ("University Studies", "CAS", [
        "1101 First-Year Seminar", "1102 Academic Success Strategies",
        "1201 Career Exploration", "2101 Peer Mentoring", "3101 Career Readiness Seminar",
    ]),
    "ENGL": ("English", "CAS", [
        "1301 Composition I", "1302 Composition II", "2307 Introduction to Creative Writing",
        "2321 British Literature I", "2322 British Literature II", "2327 American Literature I",
        "2328 American Literature II", "2332 World Literature I", "2333 World Literature II",
        "2341 Introduction to Literary Studies", "3303 Fiction Writing", "3304 Poetry Writing",
        "3311 Advanced Composition", "3320 Shakespeare", "3330 The Novel",
        "3340 Literary Theory and Criticism", "3350 Technical and Professional Writing",
        "3360 Literature of the Bible", "4310 Studies in Romanticism", "4320 Victorian Literature",
        "4330 Modern and Contemporary Poetry", "4340 History of the English Language",
        "4350 Young Adult Literature",
    ]),
    "MATH": ("Mathematics", "CAS", [
        "1314 College Algebra", "1316 Trigonometry", "1325 Business Calculus",
        "1332 Contemporary Mathematics", "1342 Elementary Statistics",
        "1350 Mathematics for Elementary Teachers I", "1351 Mathematics for Elementary Teachers II",
        "2318 Linear Algebra", "2320 Differential Equations", "2413 Calculus I", "2414 Calculus II",
        "2415 Calculus III", "3310 Discrete Mathematics", "3320 Introduction to Proof",
        "3330 Probability", "3340 Mathematical Statistics", "3350 Geometry",
        "3360 History of Mathematics", "4310 Real Analysis", "4320 Abstract Algebra",
        "4330 Numerical Analysis", "4340 Complex Variables",
    ]),
    "BIOL": ("Biology", "CAS", [
        "1308 Biology for Non-Majors", "1406 General Biology I", "1407 General Biology II",
        "2401 Anatomy and Physiology I", "2402 Anatomy and Physiology II",
        "2420 Microbiology for Health Professions", "3320 Genetics", "3330 Cell Biology",
        "3340 Ecology", "3350 Evolutionary Biology", "3360 Developmental Biology",
        "3410 Microbiology", "3420 Vertebrate Zoology", "3430 Plant Biology",
        "4320 Molecular Biology", "4330 Immunology", "4340 Neurobiology", "4350 Bioinformatics",
    ]),
    "CHEM": ("Chemistry", "CAS", [
        "1305 Chemistry in Everyday Life", "1411 General Chemistry I", "1412 General Chemistry II",
        "2123 Organic Chemistry Laboratory I", "2323 Organic Chemistry I",
        "2325 Organic Chemistry II", "3320 Physical Chemistry I", "3321 Physical Chemistry II",
        "3410 Analytical Chemistry", "3430 Instrumental Analysis", "4310 Inorganic Chemistry",
        "4320 Biochemistry I", "4321 Biochemistry II", "4330 Medicinal Chemistry",
    ]),
    "PHYS": ("Physics", "CAS", [
        "1305 Conceptual Physics", "1401 College Physics I", "1402 College Physics II",
        "1411 Introductory Astronomy", "2425 University Physics I", "2426 University Physics II",
        "3310 Modern Physics", "3320 Classical Mechanics", "3330 Electricity and Magnetism",
        "3340 Optics", "4310 Quantum Mechanics", "4320 Thermal Physics",
    ]),
    "PSYC": ("Psychology", "CAS", [
        "2301 General Psychology", "2314 Lifespan Development",
        "2317 Statistics for the Behavioral Sciences", "2320 Psychology of Adjustment",
        "3310 Research Methods in Psychology", "3320 Abnormal Psychology",
        "3330 Social Psychology", "3340 Cognitive Psychology", "3350 Biological Psychology",
        "3360 Psychology of Religion", "3370 Child Development",
        "4310 History and Systems of Psychology", "4320 Theories of Counseling",
        "4330 Psychological Testing", "4340 Health Psychology",
    ]),
    "HIST": ("History", "CAS", [
        "1301 United States History I", "1302 United States History II",
        "2311 Western Civilization I", "2312 Western Civilization II", "2321 World History I",
        "2322 World History II", "3310 Historiography and Methods", "3320 Colonial America",
        "3330 Civil War and Reconstruction", "3340 Modern Europe", "3350 History of Christianity",
        "3360 The Ancient Near East", "4310 American Religious History",
        "4320 Latin American History", "4330 The Reformation Era", "4340 Twentieth-Century America",
    ]),
    "COMM": ("Communication", "CAS", [
        "1307 Introduction to Mass Communication", "1311 Public Speaking",
        "2310 Interpersonal Communication", "2320 Media Writing", "2330 Small Group Communication",
        "3310 Communication Theory", "3320 Organizational Communication",
        "3330 Intercultural Communication", "3340 Digital Media Production",
        "3350 Public Relations", "3360 Broadcast Journalism", "4310 Persuasion",
        "4320 Communication Research Methods", "4330 Crisis Communication",
    ]),
    "POLS": ("Political Science", "CAS", [
        "2305 American Government", "2306 State and Local Government", "3310 Political Theory",
        "3320 International Relations", "3330 Constitutional Law", "3340 Comparative Politics",
        "3350 American Political Thought", "3360 Public Administration", "4310 Public Policy",
        "4320 Faith and Public Life", "4330 Congress and the Presidency", "4340 Law and Society",
    ]),
    "SOCI": ("Sociology", "CAS", [
        "1301 Introduction to Sociology", "2310 Social Problems", "2320 Marriage and Family",
        "3310 Social Theory", "3320 Race and Ethnicity", "3330 Sociology of the Family",
        "3340 Social Research Methods", "3350 Urban Sociology", "4310 Sociology of Religion",
        "4320 Social Stratification", "4330 Deviance and Social Control",
    ]),
    "CRIJ": ("Criminal Justice", "CAS", [
        "1301 Introduction to Criminal Justice", "1307 Crime in America",
        "2313 Correctional Systems", "2314 Criminal Investigation",
        "2328 Police Systems and Practices", "3310 Criminology", "3320 Criminal Law",
        "3330 Juvenile Justice", "3340 Criminal Procedure", "4310 Ethics in Criminal Justice",
        "4320 Victimology", "4330 Restorative Justice",
    ]),
    "ENVS": ("Environmental Science", "CAS", [
        "1401 Environmental Science I", "1402 Environmental Science II", "2310 Earth Systems",
        "3310 Environmental Ethics and Stewardship", "3320 Environmental Policy",
        "3430 Geographic Information Systems", "4310 Conservation Biology",
        "4320 Water Resources", "4330 Climate Science",
        "4340 Field Methods in Environmental Science",
    ]),
    "PHIL": ("Philosophy", "CAS", [
        "2301 Introduction to Philosophy", "2303 Logic", "2306 Ethics",
        "3310 Philosophy of Religion", "3320 Ancient Philosophy", "3330 Modern Philosophy",
        "3340 Political Philosophy", "4310 Philosophy of Science", "4320 Christian Philosophy",
    ]),
    "SPAN": ("Spanish", "CAS", [
        "1411 Elementary Spanish I", "1412 Elementary Spanish II", "2311 Intermediate Spanish I",
        "2312 Intermediate Spanish II", "3310 Spanish Conversation", "3320 Hispanic Cultures",
        "3330 Spanish for Health Professions", "4310 Spanish Literature",
    ]),
    "BUSI": ("Business", "COB", [
        "1301 Introduction to Business", "2305 Business Statistics",
        "2320 Business Information Systems", "3310 Legal Environment of Business",
        "3320 Business Communication", "3330 Business Ethics", "3340 International Business",
        "4310 Business Analytics", "4320 Faith and Work",
    ]),
    "ACCT": ("Accounting", "COB", [
        "2301 Principles of Financial Accounting", "2302 Principles of Managerial Accounting",
        "3310 Intermediate Accounting I", "3311 Intermediate Accounting II",
        "3320 Cost Accounting", "3330 Accounting Information Systems",
        "3340 Nonprofit and Governmental Accounting", "4310 Auditing",
        "4320 Federal Income Tax", "4330 Advanced Accounting",
    ]),
    "FINC": ("Finance", "COB", [
        "3310 Business Finance", "3320 Investments", "3330 Financial Markets and Institutions",
        "3340 Real Estate Finance", "4310 Corporate Finance", "4320 International Finance",
        "4330 Personal Financial Planning", "4340 Risk Management and Insurance",
    ]),
    "MKTG": ("Marketing", "COB", [
        "3301 Principles of Marketing", "3310 Consumer Behavior", "3320 Digital Marketing",
        "3330 Marketing Research", "3340 Professional Selling", "4310 Marketing Strategy",
        "4320 Brand Management", "4330 Social Media Marketing",
    ]),
    "MGMT": ("Management", "COB", [
        "3301 Principles of Management", "3320 Organizational Behavior",
        "3330 Human Resource Management", "3340 Leadership", "3350 Project Management",
        "4310 Operations Management", "4320 Entrepreneurship", "4330 Nonprofit Management",
        "4350 Strategic Management",
    ]),
    "ECON": ("Economics", "COB", [
        "2301 Principles of Macroeconomics", "2302 Principles of Microeconomics",
        "3310 Money and Banking", "3320 Intermediate Microeconomics",
        "3330 Economic Development", "4310 Econometrics", "4320 Public Finance",
    ]),
    "SPMT": ("Sport Management", "COB", [
        "1301 Introduction to Sport Management", "2310 Sport in Society", "3310 Sport Marketing",
        "3320 Sport Facility and Event Management", "3330 Sport Law", "4310 Sport Finance",
        "4320 Sport Governance",
    ]),
    "EGR": ("Engineering", "CEC", [
        "1201 Introduction to Engineering", "1304 Engineering Graphics", "2301 Statics",
        "2332 Dynamics", "2334 Mechanics of Materials", "3310 Engineering Economics",
        "3320 Engineering Ethics and Society", "3330 Numerical Methods for Engineers",
    ]),
    "MEEN": ("Mechanical Engineering", "CEC", [
        "3310 Thermodynamics I", "3311 Thermodynamics II", "3320 Fluid Mechanics",
        "3330 Heat Transfer", "3340 Machine Design", "3350 Manufacturing Processes",
        "3360 Materials Science", "4310 Mechanical Vibrations", "4320 Control Systems",
        "4330 Finite Element Analysis", "4340 Renewable Energy Systems",
        "4380 Senior Design I", "4381 Senior Design II",
    ]),
    "ELEN": ("Electrical Engineering", "CEC", [
        "2310 Circuit Analysis I", "2311 Circuit Analysis II", "3310 Electronics I",
        "3311 Electronics II", "3320 Signals and Systems", "3330 Electromagnetics",
        "3340 Digital Logic Design", "3350 Microcontrollers", "4310 Power Systems",
        "4320 Communication Systems", "4330 Embedded Systems", "4380 Senior Design I",
        "4381 Senior Design II",
    ]),
    "CVEN": ("Civil Engineering", "CEC", [
        "2310 Civil Engineering Materials", "2320 Surveying", "3310 Structural Analysis",
        "3320 Geotechnical Engineering", "3330 Hydraulics", "3340 Transportation Engineering",
        "3350 Construction Management", "4310 Reinforced Concrete Design",
        "4320 Environmental Engineering", "4330 Steel Design", "4380 Senior Design I",
        "4381 Senior Design II",
    ]),
    "CSCI": ("Computer Science", "CEC", [
        "1301 Introduction to Computing", "1436 Programming Fundamentals I",
        "1437 Programming Fundamentals II", "2310 Discrete Structures", "2320 Data Structures",
        "2330 Computer Organization", "3310 Algorithms", "3320 Operating Systems",
        "3330 Database Systems", "3340 Software Engineering", "3350 Computer Networks",
        "3360 Web Application Development", "3370 Mobile App Development",
        "4310 Programming Languages", "4320 Computer Security", "4330 Compilers",
        "4340 Data Mining", "4380 Senior Capstone Project",
    ]),
    "INFT": ("Information Technology", "CEC", [
        "1310 Introduction to Information Technology", "2310 Networking Fundamentals",
        "2320 Web Development", "2330 Systems Administration", "3310 Database Administration",
        "3320 Cloud Infrastructure", "3330 IT Project Management", "3340 IT Service Management",
        "4310 Enterprise Systems", "4380 IT Capstone",
    ]),
    "CYBR": ("Cybersecurity", "CEC", [
        "2310 Principles of Cybersecurity", "3310 Network Security", "3320 Ethical Hacking",
        "3330 Digital Forensics", "3340 Applied Cryptography", "4310 Security Operations",
        "4320 Cyber Law and Policy", "4380 Cybersecurity Capstone",
    ]),
    "EDUC": ("Education", "COE", [
        "1301 Introduction to the Teaching Profession", "2310 Educational Psychology",
        "3310 Classroom Management", "3320 Instructional Technology",
        "3330 Assessment of Learning", "3340 Teaching English Learners",
        "4310 Faith and Learning in the Classroom", "4690 Student Teaching",
    ]),
    "EDEL": ("Elementary Education", "COE", [
        "3310 Foundations of Reading", "3320 Teaching Elementary Mathematics",
        "3330 Teaching Elementary Science", "3340 Children's Literature",
        "3350 Teaching Elementary Social Studies", "4310 Literacy Assessment and Intervention",
        "4320 Early Childhood Education",
    ]),
    "EDSE": ("Secondary Education", "COE", [
        "3310 Secondary Curriculum and Methods", "3320 Content Area Literacy",
        "3330 Adolescent Development", "4310 Methods for Secondary Mathematics and Science",
        "4320 Methods for Secondary Humanities",
    ]),
    "EDSP": ("Special Education", "COE", [
        "2310 Introduction to Exceptional Learners", "3310 Positive Behavior Intervention",
        "3320 Assessment in Special Education", "3330 Instructional Strategies for Diverse Learners",
        "4310 Collaboration and Transition Planning", "4320 Autism Spectrum Disorders",
    ]),
    "KINE": ("Kinesiology", "COE", [
        "1101 Lifetime Wellness", "1102 Fitness Walking", "1103 Weight Training", "1104 Tennis",
        "1301 Foundations of Kinesiology", "2310 Motor Learning", "2356 First Aid and CPR",
        "3310 Biomechanics", "3320 Exercise Physiology", "3330 Adapted Physical Activity",
        "3340 Coaching Theory", "4310 Measurement and Evaluation",
        "4320 Teaching Physical Education",
    ]),
    "NURS": ("Nursing", "CNH", [
        "2310 Foundations of Nursing Practice", "2320 Health Assessment", "2330 Pharmacology",
        "3330 Maternal and Newborn Nursing", "3340 Pediatric Nursing",
        "3350 Mental Health Nursing", "3410 Adult Health Nursing I",
        "3420 Adult Health Nursing II", "4310 Community Health Nursing",
        "4320 Nursing Leadership and Management", "4330 Evidence-Based Practice",
        "4440 Capstone Practicum",
    ]),
    "HLSC": ("Health Sciences", "CNH", [
        "1301 Introduction to the Health Professions", "2310 Nutrition",
        "2320 Medical Terminology", "3310 Health Care Systems", "3330 Health Promotion",
        "3340 Health Care Administration", "4310 Health Care Ethics",
        "4320 Research in Health Sciences",
    ]),
    "EXSC": ("Exercise Science", "CNH", [
        "2310 Introduction to Exercise Science", "3310 Physiology of Exercise",
        "3320 Kinesiology and Biomechanics", "3330 Strength and Conditioning",
        "3340 Exercise Testing and Prescription", "4310 Clinical Exercise Physiology",
        "4320 Sports Nutrition",
    ]),
    "PUBH": ("Public Health", "CNH", [
        "1301 Introduction to Public Health", "2310 Global Health", "3310 Epidemiology",
        "3320 Biostatistics", "3330 Environmental Health", "3340 Health Policy",
        "4310 Program Planning and Evaluation", "4320 Community Health",
    ]),
    "SOWK": ("Social Work", "CNH", [
        "1301 Introduction to Social Work", "2310 Social Welfare Policy",
        "3310 Human Behavior in the Social Environment", "3320 Practice with Individuals",
        "3330 Practice with Groups and Families", "3340 Research for Social Work Practice",
        "4310 Practice with Communities", "4690 Field Practicum",
    ]),
    "BIBL": ("Biblical Studies", "CTA", [
        "1301 Old Testament Survey", "1302 New Testament Survey", "2310 Biblical Interpretation",
        "2320 The Pentateuch", "2350 New Testament Greek I", "2351 New Testament Greek II",
        "3310 The Gospels", "3320 Pauline Epistles", "3330 The Prophets",
        "3340 Wisdom Literature", "3350 Biblical Hebrew I", "4310 Biblical Theology",
        "4320 Acts and the Early Church",
    ]),
    "THEO": ("Theology", "CTA", [
        "2301 Introduction to Christian Theology", "3310 Systematic Theology I",
        "3311 Systematic Theology II", "3320 Christian Ethics", "3330 Church History I",
        "3331 Church History II", "3340 World Religions", "4310 Apologetics",
        "4320 Theology of Mission",
    ]),
    "MINS": ("Christian Ministry", "CTA", [
        "1301 Introduction to Christian Ministry", "2310 Spiritual Formation",
        "3310 Pastoral Care and Counseling", "3320 Homiletics", "3330 Youth and Family Ministry",
        "3340 Leadership in Ministry", "4320 Missions and Evangelism",
        "4330 Church Administration",
    ]),
    "MUSC": ("Music", "CTA", [
        "1116 Aural Skills I", "1117 Aural Skills II", "1150 Concert Choir", "1181 Class Piano I",
        "1182 Class Piano II", "1306 Music Appreciation", "1311 Music Theory I",
        "1312 Music Theory II", "2310 Music History I", "2311 Music Theory III",
        "2312 Music Theory IV", "2320 Music History II", "3310 Conducting",
        "3320 Orchestration", "3330 Hymnology", "4110 Senior Recital",
    ]),
    "WRSP": ("Worship Arts", "CTA", [
        "1301 Introduction to Worship Arts", "2310 Worship Leadership",
        "3310 Theology of Worship", "3320 Worship Technology and Production", "3330 Songwriting",
        "4310 Planning Worship Services", "4320 Worship Practicum",
    ]),
    "ARTS": ("Art and Design", "CTA", [
        "1301 Art Appreciation", "1303 Art History I", "1304 Art History II", "1311 Design I",
        "1312 Design II", "1316 Drawing I", "1317 Drawing II", "2316 Painting I",
        "2326 Sculpture I", "2348 Digital Photography", "3310 Graphic Design I",
        "3320 Graphic Design II", "3330 Printmaking", "3340 Illustration",
        "4310 Senior Exhibition",
    ]),
    "THEA": ("Theatre", "CTA", [
        "1310 Introduction to Theatre", "1351 Acting I", "2310 Stagecraft",
        "2336 Voice and Diction", "2351 Acting II", "3310 Directing", "3320 Theatre History I",
        "3321 Theatre History II", "3330 Playwriting", "4310 Theatre Production Practicum",
    ]),
}

# Courses every subject (except UNIV) also lists; numbers never collide with
# the hand-written ones (checked at generation time).
GENERIC_COURSES: list[tuple[str, str]] = [
    ("2190", "Honors Colloquium in {name}"),
    ("2390", "Topics in {name}"),
    ("3390", "Special Topics in {name}"),
    ("3391", "Study Abroad in {name}"),
    ("4190", "Directed Study in {name}"),
    ("4390", "Independent Research in {name}"),
    ("4391", "Internship in {name}"),
    ("4392", "Senior Seminar in {name}"),
    ("4393", "Advanced Topics in {name}"),
]

# Graded pass/no pass (P or NP) instead of letter grades.
PASS_FAIL: set[str] = {
    "UNIV 1101", "KINE 1101", "KINE 1102", "KINE 1103", "KINE 1104", "MUSC 1150",
    "EDUC 4690", "SOWK 4690", "UNIV 2101",
}
PASS_FAIL_GENERIC_NUMBERS: set[str] = {"4391"}

# Explicit prerequisites (all must be passed). Courses at the 3000 or 4000
# level that are not listed here require their subject's gateway course.
PREREQS: dict[str, list[str]] = {
    "ENGL 1302": ["ENGL 1301"],
    "MATH 1316": ["MATH 1314"], "MATH 1325": ["MATH 1314"], "MATH 1351": ["MATH 1350"],
    "MATH 2318": ["MATH 2413"], "MATH 2414": ["MATH 2413"], "MATH 2415": ["MATH 2414"],
    "MATH 2320": ["MATH 2414"],
    "BIOL 1407": ["BIOL 1406"], "BIOL 2402": ["BIOL 2401"], "BIOL 3410": ["BIOL 1407"],
    "BIOL 4320": ["BIOL 3320"],
    "CHEM 1412": ["CHEM 1411"], "CHEM 2323": ["CHEM 1412"], "CHEM 2123": ["CHEM 1412"],
    "CHEM 2325": ["CHEM 2323"], "CHEM 3321": ["CHEM 3320"], "CHEM 4320": ["CHEM 2323"],
    "CHEM 4321": ["CHEM 4320"],
    "PHYS 1402": ["PHYS 1401"], "PHYS 2425": ["MATH 2413"], "PHYS 2426": ["PHYS 2425"],
    "PSYC 3310": ["PSYC 2317"],
    "ACCT 2302": ["ACCT 2301"], "ACCT 3311": ["ACCT 3310"], "ACCT 4310": ["ACCT 3311"],
    "FINC 3310": ["ACCT 2301"],
    "EGR 2301": ["PHYS 2425"], "EGR 2332": ["EGR 2301"], "EGR 2334": ["EGR 2301"],
    "MEEN 3310": ["EGR 2301"], "MEEN 3311": ["MEEN 3310"], "MEEN 3320": ["MEEN 3310"],
    "MEEN 3330": ["MEEN 3320"], "MEEN 3340": ["EGR 2334"], "MEEN 3350": ["EGR 1304"],
    "MEEN 3360": ["CHEM 1411"], "MEEN 4310": ["EGR 2332"], "MEEN 4320": ["MATH 2320"],
    "MEEN 4330": ["EGR 2334"], "MEEN 4340": ["MEEN 3310"],
    "MEEN 4380": ["MEEN 3330", "MEEN 3340"], "MEEN 4381": ["MEEN 4380"],
    "ELEN 2310": ["MATH 2414"], "ELEN 2311": ["ELEN 2310"], "ELEN 3311": ["ELEN 3310"],
    "ELEN 4380": ["ELEN 3320"], "ELEN 4381": ["ELEN 4380"],
    "CVEN 3310": ["EGR 2334"], "CVEN 4310": ["CVEN 3310"], "CVEN 4330": ["CVEN 3310"],
    "CVEN 4380": ["CVEN 3310"], "CVEN 4381": ["CVEN 4380"],
    "CSCI 1437": ["CSCI 1436"], "CSCI 2310": ["CSCI 1436"], "CSCI 2320": ["CSCI 1437"],
    "CSCI 2330": ["CSCI 1437"], "CSCI 3310": ["CSCI 2320", "CSCI 2310"],
    "CSCI 3320": ["CSCI 2330"], "CSCI 3330": ["CSCI 2320"], "CSCI 3340": ["CSCI 2320"],
    "CSCI 4380": ["CSCI 3340"],
    "CYBR 2310": ["CSCI 1436"],
    "EDUC 4690": ["EDUC 3310"],
    "NURS 2310": ["BIOL 2401"], "NURS 3420": ["NURS 3410"], "NURS 4440": ["NURS 3420"],
    "BIBL 2351": ["BIBL 2350"],
    "MUSC 1117": ["MUSC 1116"], "MUSC 1182": ["MUSC 1181"], "MUSC 1312": ["MUSC 1311"],
    "MUSC 2311": ["MUSC 1312"], "MUSC 2312": ["MUSC 2311"],
    "ARTS 1312": ["ARTS 1311"], "ARTS 1317": ["ARTS 1316"],
    "THEA 2351": ["THEA 1351"],
    "SPAN 1412": ["SPAN 1411"], "SPAN 2311": ["SPAN 1412"], "SPAN 2312": ["SPAN 2311"],
}

# Subject gateway: what a 3000/4000-level course without an explicit entry requires.
GATEWAY: dict[str, str] = {
    "ENGL": "ENGL 1302", "MATH": "MATH 2413", "BIOL": "BIOL 1406", "CHEM": "CHEM 1412",
    "PHYS": "PHYS 2425", "PSYC": "PSYC 2301", "HIST": "HIST 1301", "COMM": "COMM 1311",
    "POLS": "POLS 2305", "SOCI": "SOCI 1301", "CRIJ": "CRIJ 1301", "ENVS": "ENVS 1401",
    "PHIL": "PHIL 2301", "SPAN": "SPAN 1412", "BUSI": "BUSI 1301", "ACCT": "ACCT 2301",
    "FINC": "FINC 3310", "MKTG": "MKTG 3301", "MGMT": "MGMT 3301", "ECON": "ECON 2301",
    "SPMT": "SPMT 1301", "EGR": "EGR 2301", "MEEN": "MEEN 3310", "ELEN": "ELEN 2310",
    "CVEN": "CVEN 2310", "CSCI": "CSCI 1437", "INFT": "INFT 1310", "CYBR": "CYBR 2310",
    "EDUC": "EDUC 1301", "EDEL": "EDUC 1301", "EDSE": "EDUC 1301", "EDSP": "EDSP 2310",
    "KINE": "KINE 1301", "NURS": "NURS 2310", "HLSC": "HLSC 1301", "EXSC": "EXSC 2310",
    "PUBH": "PUBH 1301", "SOWK": "SOWK 1301", "BIBL": "BIBL 2310", "THEO": "THEO 2301",
    "MINS": "MINS 1301", "MUSC": "MUSC 1311", "WRSP": "WRSP 1301", "ARTS": "ARTS 1311",
    "THEA": "THEA 1310",
}

# Core curriculum (every program). The math course depends on the program's
# math track, and the science course is added only for programs that do not
# already require a four-hour laboratory science.
CORE_COMMON: list[str] = [
    "UNIV 1101", "ENGL 1301", "ENGL 1302", "COMM 1311", "BIBL 1301", "BIBL 1302",
    "THEO 2301", "HIST 1301", "KINE 1101", "ARTS 1301", "PSYC 2301",
]
CORE_MATH: dict[str, str] = {"algebra": "MATH 1314", "stem": "MATH 2413"}
CORE_SCIENCE = "BIOL 1308"

_BUSINESS_COMMON = (
    "BUSI 1301 ACCT 2301 ACCT 2302 ECON 2301 ECON 2302 BUSI 2305 BUSI 3310 "
    "FINC 3310 MKTG 3301 MGMT 3301 MGMT 4350"
)
_ENGINEERING_COMMON = (
    "MATH 2414 MATH 2415 MATH 2320 PHYS 2425 PHYS 2426 CHEM 1411 EGR 1201 EGR 1304"
)
_EDUCATION_COMMON = "EDUC 1301 EDUC 2310 EDUC 3310 EDUC 3320 EDUC 3330 EDUC 4690"

# major code, program name, degree, award level, college, elective subject,
# entering popularity weight, math track, requirements ("support" courses are
# outside the program's own subject; the generator labels them).
PROGRAMS: list[tuple[str, str, str, str, str, str | None, float, str, str]] = [
    ("BIOL", "Biology", "BS", "Bachelor", "CAS", "BIOL", 5.5, "algebra",
     "BIOL 1406 BIOL 1407 CHEM 1411 CHEM 1412 CHEM 2323 CHEM 2325 BIOL 2401 BIOL 3320 "
     "BIOL 3330 BIOL 3340 BIOL 3410 BIOL 4320 MATH 1342 PHYS 1401"),
    ("CHEM", "Chemistry", "BS", "Bachelor", "CAS", "CHEM", 1.0, "stem",
     "CHEM 1411 CHEM 1412 CHEM 2323 CHEM 2325 CHEM 2123 CHEM 3410 CHEM 3320 CHEM 3321 "
     "CHEM 4310 CHEM 4320 BIOL 1406 PHYS 2425 PHYS 2426 MATH 2414"),
    ("MATH", "Mathematics", "BS", "Bachelor", "CAS", "MATH", 0.9, "stem",
     "MATH 2414 MATH 2415 MATH 2318 MATH 2320 MATH 3310 MATH 3320 MATH 3330 MATH 4310 "
     "MATH 4320 MATH 4330 CSCI 1436 PHYS 2425"),
    ("PSYC", "Psychology", "BS", "Bachelor", "CAS", "PSYC", 6.0, "algebra",
     "PSYC 2314 PSYC 2317 PSYC 3310 PSYC 3320 PSYC 3330 PSYC 3340 PSYC 3350 PSYC 4310 "
     "PSYC 4320 PSYC 4330"),
    ("ENGL", "English", "BA", "Bachelor", "CAS", "ENGL", 1.3, "algebra",
     "ENGL 2321 ENGL 2322 ENGL 2327 ENGL 2328 ENGL 2341 ENGL 3311 ENGL 3320 ENGL 3340 "
     "ENGL 4330 ENGL 4340 ENGL 3303 SPAN 1411 SPAN 1412"),
    ("HIST", "History", "BA", "Bachelor", "CAS", "HIST", 1.1, "algebra",
     "HIST 1302 HIST 2311 HIST 2312 HIST 3310 HIST 3320 HIST 3330 HIST 3340 HIST 3350 "
     "HIST 4310 HIST 4320 POLS 2305"),
    ("COMM", "Communication", "BA", "Bachelor", "CAS", "COMM", 3.0, "algebra",
     "COMM 1307 COMM 2310 COMM 2320 COMM 3310 COMM 3320 COMM 3330 COMM 3340 COMM 3350 "
     "COMM 4310 COMM 4320 MKTG 3301"),
    ("POLS", "Political Science", "BA", "Bachelor", "CAS", "POLS", 1.2, "algebra",
     "POLS 2305 POLS 2306 POLS 3310 POLS 3320 POLS 3330 POLS 3340 POLS 4310 POLS 4320 "
     "HIST 1302 ECON 2301"),
    ("SOCI", "Sociology", "BA", "Bachelor", "CAS", "SOCI", 0.9, "algebra",
     "SOCI 1301 SOCI 2310 SOCI 3310 SOCI 3320 SOCI 3330 SOCI 3340 SOCI 4310 SOCI 4320 "
     "PSYC 2317 CRIJ 1301"),
    ("CRIJ", "Criminal Justice", "BS", "Bachelor", "CAS", "CRIJ", 3.0, "algebra",
     "CRIJ 1301 CRIJ 1307 CRIJ 2313 CRIJ 2314 CRIJ 2328 CRIJ 3310 CRIJ 3320 CRIJ 3330 "
     "CRIJ 4310 CRIJ 4320 SOCI 1301 PSYC 2317"),
    ("ENVS", "Environmental Science", "BS", "Bachelor", "CAS", "ENVS", 1.0, "algebra",
     "ENVS 1401 ENVS 1402 ENVS 2310 BIOL 3340 ENVS 3320 ENVS 3430 ENVS 4310 ENVS 4320 "
     "CHEM 1411 CHEM 1412 BIOL 1406 MATH 1342"),
    ("GNST", "General Studies", "AA", "Associate", "CAS", None, 2.0, "algebra",
     "SOCI 1301 ECON 2301 ENGL 2332"),
    ("BUAD", "Business Administration", "BBA", "Bachelor", "COB", "BUSI", 7.0, "algebra",
     _BUSINESS_COMMON + " BUSI 2320 BUSI 3330 MGMT 3320 BUSI 3340 MATH 1325"),
    ("ACCT", "Accounting", "BBA", "Bachelor", "COB", "ACCT", 2.5, "algebra",
     _BUSINESS_COMMON + " ACCT 3310 ACCT 3311 ACCT 3320 ACCT 3330 ACCT 4310 ACCT 4320"),
    ("FINC", "Finance", "BBA", "Bachelor", "COB", "FINC", 2.0, "algebra",
     _BUSINESS_COMMON + " FINC 3320 FINC 3330 FINC 4310 FINC 4320 FINC 4330 ECON 3310"),
    ("MKTG", "Marketing", "BBA", "Bachelor", "COB", "MKTG", 2.0, "algebra",
     _BUSINESS_COMMON + " MKTG 3310 MKTG 3320 MKTG 3330 MKTG 4310 MKTG 4320 COMM 3350"),
    ("MGMT", "Management", "BBA", "Bachelor", "COB", "MGMT", 2.0, "algebra",
     _BUSINESS_COMMON + " MGMT 3320 MGMT 3330 MGMT 3340 MGMT 4310 MGMT 4320 MGMT 4330"),
    ("SPMT", "Sport Management", "BS", "Bachelor", "COB", "SPMT", 2.0, "algebra",
     "SPMT 1301 SPMT 2310 SPMT 3310 SPMT 3320 SPMT 3330 SPMT 4310 ACCT 2301 ECON 2302 "
     "MGMT 3301 MKTG 3301"),
    ("MEEN", "Mechanical Engineering", "BS", "Bachelor", "CEC", "MEEN", 4.0, "stem",
     _ENGINEERING_COMMON + " EGR 2301 EGR 2332 EGR 2334 MEEN 3310 MEEN 3311 MEEN 3320 "
     "MEEN 3330 MEEN 3340 MEEN 3350 MEEN 4310 MEEN 4380"),
    ("ELEN", "Electrical Engineering", "BS", "Bachelor", "CEC", "ELEN", 1.6, "stem",
     _ENGINEERING_COMMON + " ELEN 2310 ELEN 2311 ELEN 3310 ELEN 3320 ELEN 3330 ELEN 3340 "
     "ELEN 4310 ELEN 4320 ELEN 4380 CSCI 1436"),
    ("CVEN", "Civil Engineering", "BS", "Bachelor", "CEC", "CVEN", 1.6, "stem",
     _ENGINEERING_COMMON + " EGR 2301 EGR 2334 CVEN 2310 CVEN 3310 CVEN 3320 CVEN 3330 "
     "CVEN 3340 CVEN 4310 CVEN 4320 CVEN 4380"),
    ("CSCI", "Computer Science", "BS", "Bachelor", "CEC", "CSCI", 3.2, "stem",
     "CSCI 1436 CSCI 1437 CSCI 2310 CSCI 2320 CSCI 2330 CSCI 3310 CSCI 3320 CSCI 3330 "
     "CSCI 3340 CSCI 3350 CSCI 4310 CSCI 4320 CSCI 4380 MATH 2318 MATH 2414"),
    ("INFT", "Information Technology", "BS", "Bachelor", "CEC", "INFT", 1.8, "algebra",
     "CSCI 1436 INFT 1310 INFT 2310 INFT 2320 INFT 2330 INFT 3310 INFT 3320 INFT 3330 "
     "INFT 4310 INFT 4380 MATH 1342"),
    ("CYBR", "Cybersecurity", "BS", "Bachelor", "CEC", "CYBR", 1.2, "algebra",
     "CSCI 1436 CSCI 1437 INFT 2310 CYBR 2310 CYBR 3310 CYBR 3320 CYBR 3330 CYBR 3340 "
     "CYBR 4310 CYBR 4320 CYBR 4380 MATH 1342"),
    ("EDEL", "Elementary Education", "BSE", "Bachelor", "COE", "EDEL", 4.5, "algebra",
     _EDUCATION_COMMON + " EDEL 3310 EDEL 3320 EDEL 3330 EDEL 3340 EDEL 3350 EDEL 4310 "
     "MATH 1350 MATH 1351"),
    ("EDSE", "Secondary Education", "BSE", "Bachelor", "COE", "EDSE", 1.2, "algebra",
     _EDUCATION_COMMON + " EDSE 3310 EDSE 3320 EDSE 3330 EDSE 4310 EDSE 4320"),
    ("EDSP", "Special Education", "BSE", "Bachelor", "COE", "EDSP", 1.0, "algebra",
     _EDUCATION_COMMON + " EDSP 2310 EDSP 3310 EDSP 3320 EDSP 3330 EDSP 4310 EDSP 4320"),
    ("KINE", "Kinesiology", "BS", "Bachelor", "COE", "KINE", 3.0, "algebra",
     "KINE 1301 KINE 2310 KINE 2356 KINE 3310 KINE 3320 KINE 3330 KINE 3340 KINE 4310 "
     "KINE 4320 BIOL 2401"),
    ("NURS", "Nursing", "BSN", "Bachelor", "CNH", "NURS", 9.0, "algebra",
     "BIOL 2401 BIOL 2402 BIOL 2420 CHEM 1411 MATH 1342 PSYC 2314 HLSC 2310 NURS 2310 "
     "NURS 2320 NURS 2330 NURS 3410 NURS 3420 NURS 3330 NURS 3340 NURS 3350 NURS 4310 "
     "NURS 4320 NURS 4330 NURS 4440"),
    ("HLSC", "Health Sciences", "BS", "Bachelor", "CNH", "HLSC", 3.0, "algebra",
     "BIOL 2401 BIOL 2402 HLSC 1301 HLSC 2310 HLSC 2320 HLSC 3310 HLSC 3330 HLSC 4310 "
     "HLSC 4320 MATH 1342 PSYC 2314"),
    ("EXSC", "Exercise Science", "BS", "Bachelor", "CNH", "EXSC", 3.0, "algebra",
     "BIOL 2401 BIOL 2402 EXSC 2310 EXSC 3310 EXSC 3320 EXSC 3330 EXSC 3340 EXSC 4310 "
     "EXSC 4320 HLSC 2310 MATH 1342 CHEM 1411"),
    ("PUBH", "Public Health", "BS", "Bachelor", "CNH", "PUBH", 1.5, "algebra",
     "PUBH 1301 PUBH 2310 PUBH 3310 PUBH 3320 PUBH 3330 PUBH 3340 PUBH 4310 PUBH 4320 "
     "MATH 1342 SOCI 1301 HLSC 2310"),
    ("SOWK", "Social Work", "BSW", "Bachelor", "CNH", "SOWK", 2.0, "algebra",
     "SOWK 1301 SOWK 2310 SOWK 3310 SOWK 3320 SOWK 3330 SOWK 3340 SOWK 4310 SOWK 4690 "
     "SOCI 1301 PSYC 2314 PSYC 2317"),
    ("BIBL", "Biblical Studies", "BA", "Bachelor", "CTA", "BIBL", 1.5, "algebra",
     "BIBL 2310 BIBL 2320 BIBL 2350 BIBL 2351 BIBL 3310 BIBL 3320 BIBL 3330 BIBL 3340 "
     "BIBL 4310 BIBL 4320 THEO 3310 HIST 3350"),
    ("THEO", "Theology", "BA", "Bachelor", "CTA", "THEO", 0.8, "algebra",
     "THEO 3310 THEO 3311 THEO 3320 THEO 3330 THEO 3331 THEO 4310 THEO 4320 BIBL 2310 "
     "PHIL 2301 PHIL 3310"),
    ("MINS", "Christian Ministry", "BA", "Bachelor", "CTA", "MINS", 2.0, "algebra",
     "MINS 1301 MINS 2310 MINS 3310 MINS 3320 MINS 3330 MINS 3340 MINS 4320 MINS 4391 "
     "BIBL 2310 THEO 3310 PSYC 2314"),
    ("MUSC", "Music", "BM", "Bachelor", "CTA", "MUSC", 1.5, "algebra",
     "MUSC 1311 MUSC 1312 MUSC 2311 MUSC 2312 MUSC 1116 MUSC 1117 MUSC 2310 MUSC 2320 "
     "MUSC 3310 MUSC 3320 MUSC 4110 MUSC 1181 MUSC 1182"),
    ("WRSP", "Worship Arts", "BA", "Bachelor", "CTA", "WRSP", 1.2, "algebra",
     "MUSC 1311 MUSC 1312 MUSC 1116 MUSC 1181 WRSP 1301 WRSP 2310 WRSP 3310 WRSP 3320 "
     "WRSP 3330 WRSP 4310 BIBL 2310"),
    ("ARTS", "Art and Design", "BFA", "Bachelor", "CTA", "ARTS", 1.8, "algebra",
     "ARTS 1311 ARTS 1312 ARTS 1316 ARTS 1317 ARTS 2316 ARTS 2326 ARTS 2348 ARTS 3310 "
     "ARTS 3320 ARTS 1303 ARTS 1304 ARTS 4310"),
    ("THEA", "Theatre", "BA", "Bachelor", "CTA", "THEA", 0.9, "algebra",
     "THEA 1310 THEA 1351 THEA 2351 THEA 2336 THEA 2310 THEA 3310 THEA 3320 THEA 3321 "
     "THEA 3330 THEA 4310 ENGL 3320"),
]

CREDITS_REQUIRED: dict[str, int] = {"GNST": 60, "NURS": 124, "MEEN": 128, "ELEN": 128, "CVEN": 128}
DEFAULT_CREDITS_REQUIRED = 120

# Fictional instructor names. Combinations are generated, so any match with
# a real person is coincidental.
FIRST_NAMES: list[str] = (
    "Aaron Abigail Adrian Alicia Andrea Anthony Ariana Benjamin Bethany Brandon Brianna "
    "Caleb Camila Carlos Caroline Catherine Charles Chloe Christopher Claire Daniel Danielle "
    "David Deborah Diana Dominic Eleanor Elijah Elena Emily Ethan Evelyn Felix Gabriel Grace "
    "Hannah Hector Isaac Isabel Jacob Janelle Jasmine Jeremiah Joanna Jonah Joseph Julia "
    "Julian Karen Katherine Kevin Laura Leah Lillian Lucas Lydia Marcus Margaret Maria Mark "
    "Martha Matthew Megan Micah Miriam Monica Nathan Naomi Nicholas Olivia Owen Patricia "
    "Paul Peter Priscilla Rachel Raymond Rebecca Ruth Samuel Sarah Simon Sophia Stephen "
    "Susanna Teresa Thomas Timothy Valerie Victor Vivian Wesley Zachary"
).split()
LAST_NAMES: list[str] = (
    "Abernathy Aldridge Ashford Bancroft Barlow Beckett Bellamy Benton Blackwood Bramwell "
    "Calloway Carrington Castellano Chandler Coleridge Corbett Crandall Dalton Delacroix "
    "Donnelly Drummond Eastwood Ellison Emberly Fairchild Farrow Fennimore Galloway Garrison "
    "Greenhalgh Hadley Halloway Hartwell Hawthorne Holloway Huxley Ingram Jarrell Kendrick "
    "Kingsley Lancaster Langford Larkin Lockwood Lowell Mabry Marchetti Merriweather "
    "Montague Nakamura Northcott Oakley Okafor Orwell Pemberton Pendleton Prescott Quintero "
    "Radcliffe Ramsay Redmond Rhodes Rutherford Sandoval Seaton Shelby Sinclair Stafford "
    "Sterling Stratton Sutherland Talbot Thornbury Trevino Underhill Valdez Vance Wakefield "
    "Wexford Whitaker Whitmore Winslow Woodward Yardley Yarrow Zamora Achebe Brightwater "
    "Castillo Delgado Esposito Faraday Gutierrez Hollander Ibarra Jennings Kowalczyk "
    "Lindqvist Mendoza Novak Oyelaran Petrov Quinlan Rosales Szabo Takahashi Ueda Varga"
).split()
