@router.post("/text-chunk")
async def translate_text_chunk(body: TextChunkRequest) -> dict:
    original = (body.original or "").strip()
    if not original:
        raise HTTPException(status_code=400, detail="Text chunk must not be empty.")

    student = await db.get_student(body.student_id) or {}
    grade = int(student.get("grade_level") or 1)
    age = int(student.get("age") or 10)
    translated, model_switched = await _translate_text(original, body.target_language, grade, age)
    if not translated:
        translated = original

    return {
        "index": body.index,
        "source_indices": [],
        "original": original,
        "translated": translated,
        "timestamp": body.timestamp or datetime.utcnow().strftime("%H:%M"),
        "detected_language": body.detected_language or "en",
        "model_switched": model_switched,
    }
