def calculate(rows, payload):
    corrections = payload.get("clock_corrections", {})
    corrected = [{**r, "time": round(float(r["time"]) + corrections.get(r["station"], 0), 3)} for r in rows]
    events = []
    for pick in sorted(corrected, key=lambda r: r["time"]):
        match = next((e for e in events if abs(e["origin_time"] - pick["time"]) <= 0.25 and pick["station"] not in e["stations"]), None)
        if match is None:
            events.append({"event_id": f"evt-{len(events)+1:03d}", "origin_time": pick["time"], "stations": [pick["station"]], "picks": [pick]})
        else:
            match["stations"].append(pick["station"]); match["picks"].append(pick)
    accepted = [e for e in events if len(e["stations"]) >= 2]
    return {"events": [{"event_id": e["event_id"], "origin_time": e["origin_time"], "stations": sorted(e["stations"]), "pick_count": len(e["picks"]), "source_stations": sorted(e["stations"])} for e in accepted], "rejected_pick_count": len(corrected) - sum(len(e["picks"]) for e in accepted)}
