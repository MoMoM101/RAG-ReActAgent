# Frontend Upload Parallelism — Implementation Plan (Phase 3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Remove global uploading lock so users can upload new files while previous batches are still processing.

**Architecture:** Two deletions in `documentStore.ts`, one test. UploadZone stays unchanged.

**Tech Stack:** TypeScript, Zustand, React

---

### Task 1: Remove global upload lock + add test

**Files:**
- Modify: `frontend/src/stores/documentStore.ts:120-128, 256-262`
- Modify: `frontend/src/stores/__tests__/documentStore.test.ts` (add test)

- [ ] **Step 1: Remove lock from uploadMany (lines 120-128)**

Delete this block:
```typescript
    if (get().uploading) {
      useToastStore.getState().addToast({
        type: "warning",
        message: "已有一批文件正在上传，请稍后再试",
      });
      return;
    }
```

- [ ] **Step 2: Remove lock from upload (lines 256-262)**

Delete this block:
```typescript
    if (get().uploading) {
      useToastStore.getState().addToast({
        type: "warning",
        message: "已有文件正在上传或处理，请完成后再上传",
      });
      return;
    }
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd D:/Python/subject1/RAG_Agent/frontend
npm run build 2>&1
```

Expected: build succeeds.

- [ ] **Step 4: Run frontend tests**

```bash
cd D:/Python/subject1/RAG_Agent/frontend
npm test 2>&1
```

Expected: existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/documentStore.ts
git commit -m "feat: remove global upload lock, allow parallel batch uploads"
```
