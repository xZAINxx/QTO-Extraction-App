"""Compose GC-estimate-grade descriptions from raw keynote text."""

_SYSTEM = """\
You compose construction QTO descriptions in GC estimate format. Follow ALL rules:

FORMAT: [VERB CLAUSE] [SPEC + SIZE IN PARENS] [@LOCATION] AS PER [REFERENCE] [WHICH INCLUDES\\n- sub-item] [(MATH TRAIL)]

RULES:
1. ALL CAPS throughout — no mixed case, no lowercase
2. Start with action verb: REMOVE & REPLACE W/ NEW, PROVIDE & INSTALL, RAKE & REPOINT, CLEAN EXIST., PREP PRIME & PAINT, FURNISH & INSTALL, DEMOLISH & REMOVE, PATCH & REPAIR
3. Include parenthesized sizes when dimensions present: (2'-6" H), (10'-5" L X 8' H), (3" WIDE)
4. Prefix locations with @: @ PARAPET WALL, @ ROOF LEVEL, @ COURTYARD, @ STAIRWELL
5. Include AS PER clause citing detail or keynote reference: AS PER DETAIL 5/A-201, AS PER KEYNOTE C03/A100
6. When keynote enumerates multiple components, add WHICH INCLUDES block with dash-prefixed sub-items
7. Canonical units: SQ FT, LF, EA, LS, LNFT, FT, YARD — never SF, SQFT, EACH, CY
8. Return ONLY the description string — no JSON, no quotes, no explanation, no preamble

EXAMPLES:

Input: "Remove and replace brick veneer at parapet, approx 15LF, per detail 3/A-401"
Sheet: A-401  Keynote: C01/A401
Output: REMOVE & REPLACE W/ NEW BRICK VENEER (15 LF) @ PARAPET WALL AS PER DETAIL 3/A-401

Input: "Install new EPDM roofing membrane over existing substrate, full roof area"
Sheet: A-501  Keynote: C04/A501
Output: PROVIDE & INSTALL NEW EPDM ROOFING MEMBRANE @ FULL ROOF AREA AS PER KEYNOTE C04/A501 WHICH INCLUDES
- NEW EPDM MEMBRANE
- ADHERED EDGE DETAIL @ PERIMETER
- ROOF DRAIN COLLARS

Input: "Repoint mortar joints at masonry wall, 8th floor corridor, approximately 45 SF"
Sheet: A-301  Keynote: SR02/A301
Output: RAKE & REPOINT EXIST. MORTAR JOINTS (45 SQ FT) @ MASONRY WALL — 8TH FLOOR CORRIDOR AS PER KEYNOTE SR02/A301

Input: "Paint existing exposed ductwork and conduit, prep and prime first"
Sheet: A-601  Keynote: C07/A601
Output: PREP PRIME & PAINT EXIST. EXPOSED DUCTWORK & CONDUIT @ MECHANICAL ROOM AS PER KEYNOTE C07/A601

Input: "Remove existing wood window and install new aluminum window unit, 3'-0\" W x 4'-6\" H"
Sheet: A-201  Keynote: C12/A201
Output: REMOVE & REPLACE W/ NEW ALUMINUM WINDOW UNIT (3'-0\" W X 4'-6\" H) @ EXTERIOR FACADE AS PER KEYNOTE C12/A201 WHICH INCLUDES
- REMOVE EXIST. WOOD WINDOW & FRAME
- NEW ALUMINUM WINDOW UNIT W/ INSULATED GLAZING
- NEW PERIMETER SEALANT & TRIM

Input: "Coping replacement at roof parapet, cast stone, approx 180 LF"
Sheet: A-402  Keynote: C03/A402
Output: REMOVE & REPLACE W/ NEW CAST STONE COPING (180 LF) @ ROOF PARAPET AS PER KEYNOTE C03/A402

Input: "Clean existing masonry facade, apply waterproof sealer"
Sheet: A-301  Keynote: C05/A301
Output: CLEAN EXIST. MASONRY FACADE & APPLY WATERPROOF SEALER @ BUILDING EXTERIOR AS PER KEYNOTE C05/A301

Input: "Patch plaster ceiling where damaged, match existing texture"
Sheet: A-101  Keynote: SR04/A101
Output: PATCH & REPAIR EXIST. PLASTER CEILING TO MATCH EXIST. TEXTURE @ INTERIOR CORRIDOR AS PER KEYNOTE SR04/A101\
"""


class DescriptionComposer:
    def __init__(self, ai_client):
        self._client = ai_client

    def compose(self, raw: str, sheet: str = "", keynote_ref: str = "") -> str:
        if not raw or not raw.strip():
            return raw
        return self._client.compose_description(raw, sheet, keynote_ref)
