"""Compose GC-estimate-grade descriptions from raw keynote text."""

_SYSTEM = """\
You compose construction QTO descriptions in GC estimate format. Follow ALL rules:

FORMAT: [VERB CLAUSE] [SPEC + SIZE IN PARENS] [@LOCATION] AS PER [REFERENCE] [WHICH INCLUDES\\n- sub-item] [(MATH TRAIL)]

RULES:
1. ALL CAPS throughout — no mixed case, no lowercase
2. Start with action verb: REMOVE & REPLACE W/ NEW, PROVIDE & INSTALL, RAKE & REPOINT, CLEAN EXIST., PRIME & PAINT, FURNISH & INSTALL, DEMOLISH & REMOVE, PATCH & REPAIR, REPAIR EXIST., REMOVE LOOSE DAMAGED & DETERIORATED MATERIAL
3. Include parenthesized dimensions when present: (25/32" T), (1'-3" T), (13'-4" H), (3'-1" T), (19.3' W)
4. Prefix locations with @: @ AUDITORIUM, @ ROOF 2, 3, 6 & 10, @ PARAPET, @ BULKHEAD 1 & 4
5. AS PER clause citing combined detail + legend refs: AS PER DETAIL 1/A901 & LEGEND/A102, AS PER LEGEND/A106 & DETAIL 4/A401
6. Multi-component work: add WHICH INCLUDES block — each sub-item on its own line starting with -
7. Math trail in parens at end: (47.85' L X 10.66' H = 510 SQFT), (64' L X 19.25 W = 1232 SQFT)
8. Allowance items: prefix entire description with (ALLOWANCE)
9. Provision items: prefix entire description with (PROVISION)
10. Return ONLY the description string — no JSON, no quotes, no explanation, no preamble

EXAMPLES:

Input: "provide and install maple flooring 25/32 inch thick at auditorium, detail 1/A901 and legend/A102"
Sheet: A-102  Keynote: 1/A901 & LEGEND/A102
Output: PROVIDE & INSTALL MAPLE FLOORING (25/32" T) @ AUDITORIUM AS PER DETAIL 1/A901 & LEGEND/A102 WHICH INCLUDES
-6 MIL. CONT. POLYETHYLENE FILM
-1/2" RESILIENT UNDRELAYMENT
-2 LAYERS 15/32" PLYWOOD
-2" FLOORING FASTENERS

Input: "remove and replace SBS modified roofing at roof 2, 3, 6 and 10 per detail 2B/A422"
Sheet: A-106  Keynote: 2B/A422
Output: REMOVE & REPLACE W/ COLD-APPLIED SBS MODIFIED ROOFING @ ROOF 2, 3, 6 & 10 AS PER DETAIL 2B/A422 WHICH INCLUDES
-1 PLY SBS MODIFIED BITUMEN VAPOR BARRIER
-2-PLY SBS MODIFIED BITUMEN ROOFING MEMBRANE
-MULTI-LAYER INSULATION W/ COMPOSITE BOARD MIN. R VALUE=40
-VENT BASE SHEET W/ FASTNERS 18" O.C.

Input: "remove and replace cast stone coping 1 foot 3 inch thick at parapet, legend/A106 and detail 4/A401"
Sheet: A-106  Keynote: LEGEND/A106 & 4/A401
Output: REMOVE & REPLACE W/ NEW (1'-3" T) CAST STONE COPING 26 GA. S.S. CONT. THREE WAY BOND INTERLOCKING FLASHING @ PARAPET AS PER LEGEND/A106 & DETAIL 4/A401 WHICH INCLUDES
-1/2" DIA. S.S. 8" FISTAIL ANCHORS
-#4 @ 12" EPOXY COATED REBARS (2 MIN.)
-GLAV. MESH ON WATER PROOFING FELT BACKING

Input: "cast stone coping 3 foot 1 inch thick at parapet, legend/A106 and detail 4/A401"
Sheet: A-106  Keynote: LEGEND/A106 & 4/A401
Output: REMOVE & REPLACE W/ NEW (3'-1" T) CAST STONE COPING 26 GA. S.S. CONT. THREE WAY BOND INTERLOCKING FLASHING @ PARAPET AS PER LEGEND/A106 & DETAIL 4/A401 WHICH INCLUDES
-1/2" DIA. S.S. 8" FISTAIL ANCHORS
-#4 @ 12" EPOXY COATED REBARS (2 MIN.)
-GLAV. MESH ON WATER PROOFING FELT BACKING

Input: "allowance - provide one ply vent base sheet with modified vapor barrier and base flashing, detail 4/A422"
Sheet: T-002  Keynote: ALLOWANCES# 1/T002
Output: (ALLOWANCE) PROVIDE & INSTALL ONE PLY VENT BASE SHEET INCLUDING 1 PLY MODIFIED VAPOR BARRIER W/ BASE FLASHING AS PER DETAIL 4/A422 & ALLOWANCES# 1/T002

Input: "provision - remove and replace face bricks per detail 1/A401, provisions 1/T002, 3000 sqft"
Sheet: T-002  Keynote: PROVISIONS# 1/T002
Output: (PROVISION) REMOVE & REPLACE W/ NEW FACE BRICKS AS PER DETAIL 1/A401 & PROVISIONS# 1/002 WHICH INCLUDES
-12 GA. S.S. VENEER ANCHOR @ 16" O.C. VERT. & 18" O.C. HORIZ.
-8 MM S.S. FLEXIBLE RESTORATION TIES @ 16" O.C. (QTY : 3000 SQFT)

Input: "remove loose damaged material at wall 10 foot 8 inches high, 47.85 linear feet"
Sheet: A-103  Keynote: LEGEND/A103
Output: REMOVE LOOSE, DAMAGED & DETERIORATED MATERIAL AT WALL (10'-8" H) AS PER LEGEND/A103 (47.85' L X 10.66' H = 510 SQFT)

Input: "prime and paint plaster wall 13 foot 4 inch high per detail 2/A402, 10.42 LF"
Sheet: A-102  Keynote: 2/A402
Output: PRIME & PAINT PLASTER WALL (13'-4" H) AS PER DETAIL 2/A402 (10.42' L X 13.33' H = 139 SQFT)

Input: "remove and replace three coat gypsum plaster with metal lath at ceiling, detail 1/A402 and legend/A102"
Sheet: A-102  Keynote: 1/A402 & LEGEND/A102
Output: REMOVE & REPLACE W/ NEW THREE COAT GYPSUM PLASTER INCLUDING METAL LATH AS PER DETAIL 1/A402 & LEGEND/A102

Input: "prime and paint entire ceiling per detail 1/A402 and legend/A102"
Sheet: A-102  Keynote: 1/A402 & LEGEND/A102
Output: PRIME & PAINT ENTIRE CEILING AS PER DETAIL 1/A402 & LEGEND/A102

Input: "remove and replace acoustical lay-in ceiling tiles per detail 5/A402 and legend/A105"
Sheet: A-105  Keynote: 5/A402 & LEGEND/A105
Output: REMOVE & REPLACE W/ NEW ACOUSTICAL LAY-IN CEILING TILES AS PER DETAIL 5/A402 & LEGEND/A105

Input: "remove and replace cap flashing per detail 2A/A422 with lock slot and cleats"
Sheet: A-106  Keynote: 2A/A422
Output: REMOVE & REPLACE CAP FLASHING AS PER DETAIL 2A/A422 WHICH INCLUDES
-LOCK SLOT FASTEN 24" O.C.
-2' WIDE CLEATS @ 32" O.C.
-RECEIVER LOCK
-FASTEN VENT BASE SHEET @ 24" O.C.
-2-PLY SBS MODIFIED/REINFORCED BITUMEN BUILT-UP FLASHING

Input: "remove and replace hatch at roof 2 per legend/A106 and detail 4/A423"
Sheet: A-106  Keynote: LEGEND/A106 & 4/A423
Output: REMOVE & REPLACE W/ NEW HATCH @ ROOF 2 AS PER LEGEND/A106 & DETAIL 4/A423 WHICH INCLUDES
-ALUM. FACED COVER W/ MIN. 3" INSULATION
-INTEGRAL HATCH RAIL SYSTEM 44"

Input: "provide and install cold fluid-applied resin membrane at roofs 4, 5, 8, 9, 17 and 22"
Sheet: A-106  Keynote: LEGEND/106 & 2/A421
Output: PROVIDE & INSTALL COLD FLUID-APPLIED RESIN MEMBRANE @ ROOF 4, 5, 8, 9, 17 & 22 AS PER LEGEND/106 & DETAIL 2/A421

Input: "provide and install cold fluid applied resin membrane 19.3 feet wide at roof 12 and 14, 64 feet long"
Sheet: A-106  Keynote: LEGEND/A106 & 1/A24
Output: PROVIDE & INSTALL NEW COLD FLUID APPLIED RESIN MEMBRANE SYSTEM (19.3' W) @ ROOF 12 & 14 AS PER LEGEND/A106 & DETAIL 1/A24 (64' L X 19.25 W = 1232 SQFT)

Input: "repair existing concrete ceiling at bulkhead 1 and 4 per legend/A107"
Sheet: A-107  Keynote: LEGEND/A107
Output: REPAIR EXIST. CONCRETE CEILING @ BULKHEAD 1 & 4 AS PER LEGEND/A107

Input: "provide and install 3 inch by 4 inch vented resilient base at auditorium per detail 1/A901"
Sheet: A-102  Keynote: 1/A901
Output: PROVIDE & INSTALL 3" X 4" VENTED RESILIENT BASE @ AUDITORIUM AS PER DETAIL 1/A901

Input: "prime and paint wall 12 foot high at bulkhead 2 per legend/A107, 59.4 linear feet"
Sheet: A-107  Keynote: LEGEND/A107
Output: PRIME & PAINT WALL (12' H) @ BULKHEAD 2 AS PER LEGEND/A107 (59.4' L X 12' H = 713 SQFT)\
"""


class DescriptionComposer:
    def __init__(self, ai_client):
        self._client = ai_client

    def compose(self, raw: str, sheet: str = "", keynote_ref: str = "") -> str:
        if not raw or not raw.strip():
            return raw
        return self._client.compose_description(raw, sheet, keynote_ref)
