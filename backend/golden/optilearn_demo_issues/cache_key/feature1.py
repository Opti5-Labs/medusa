# ──────────────────────────────────────────────────────────────
# Route 4 — POST /api/feature1/explain  (SSE)
# ──────────────────────────────────────────────────────────────
class ExplainRequest(BaseModel):
    material_id: str
    student_id: str
    language: str
    model_preference: str = "fast"


@router.post("/explain")
async def explain_material(body: ExplainRequest) -> StreamingResponse:
    student = await db.get_student(body.student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")

    translated_text: str = _translation_cache.get(body.material_id, "")  # type: ignore[assignment]
    if not translated_text:
        material = await db.get_material(body.material_id)
        if material and material.get("translated_text"):
            translated_text = material["translated_text"]
            _translation_cache[body.material_id] = translated_text
    if not translated_text:
        raise HTTPException(
            status_code=404, detail="Translation not found — translate the material first."
        )

    lang_name = language_prompt_label(body.language)
    prompt = _EXPLAIN_PROMPT.format(
        name=student["name"],
        age=student.get("age", 10),
        grade_level=student.get("grade_level", 1),
        language=lang_name,
        translated_text=translated_text[:4000],
    )
    source_hash = generated_cache.hash_text(translated_text)
    cache_key = generated_cache.make_cache_key(
        "material.explanation",
        {
            "material_id": body.material_id,
            "student_id": body.student_id,
            "language": body.language,
            "grade": student.get("grade_level", 1),
            "source_hash": source_hash,
        },
        prompt_version="feature1-explain-v2",
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            cached = await generated_cache.get_text(cache_key, "material.explanation")
            if cached:
                cached = normalize_ai_output(cached)
                yield _sse({"type": "token", "page": 1, "content": cached})
                yield _sse({"type": "done", "total_pages": 1, "cache_hit": True})
                return

            summary_chunks: list[str] = []
            # FUTURE: replace with fine-tuned model
            gen = route_generate(
                prompt,
                "TUTOR",
                enable_thinking=False,
                ollama_options={"num_ctx": 4096, "num_predict": 1024},
                lane="student_chat",
                feature="feature1.explain",
                profile="tutor_fast",
            )
            async for sse_event in _sse_stream_with_keepalive(gen, 1, summary_chunks.append):
                yield sse_event
            summary_text = normalize_ai_output("".join(summary_chunks))
            if summary_text:
                await db.update_material_tutor_summary(body.material_id, summary_text)
                await generated_cache.set_text(
                    cache_key,
                    "material.explanation",
                    summary_text,
                    input_hash=source_hash,
                    metadata={
                        "material_id": body.material_id,
                        "student_id": body.student_id,
                        "language": body.language,
                    },
                )
            yield _sse({"type": "done", "total_pages": 1})
        except Exception as exc:
            logger.error("Explain stream error: {}\n{}", exc, traceback.format_exc())
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────────────────────
# Route 5 — POST /api/feature1/ask  (SSE or JSON)
# ──────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    material_id: str
    student_id: str
    question: str
    language: str
    highlighted_text: str | None = None
    format: str = "stream"  # "stream" | "json"
    model_preference: str = "fast"


@router.post("/ask")
async def ask_question(body: AskRequest):
    student = await db.get_student(body.student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")

    translated_text: str = _translation_cache.get(body.material_id, "")  # type: ignore[assignment]
    if not translated_text:
        material = await db.get_material(body.material_id)
        if material and material.get("translated_text"):
            translated_text = material["translated_text"]

    context = body.highlighted_text if body.highlighted_text else translated_text[:1000]
    lang_name = language_prompt_label(body.language)

    if body.format == "json":
        context = translated_text[:500] if translated_text else "general learning material"
