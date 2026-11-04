"""Additive registration only; no lifecycle, database or supplier changes."""
import asyncio
from typing import Literal
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from .service import scene_reader


def register_http(app,service):
    router=APIRouter();reader=scene_reader(service)
    @router.get('/api/projects/{project_id}/drawing-scene')
    async def drawing_scene(project_id:str,revisionId:str,kind:Literal['schematic','pcb'],sheet:str|None=None,since:str|None=None,instancePath:str|None=None):
        scene=await asyncio.to_thread(reader.read,project_id,revisionId,kind,sheet,since,instancePath)
        return JSONResponse(scene,headers={'Cache-Control':'no-store'})
    app.include_router(router)


def register_mcp(mcp,service):
    reader=scene_reader(service)
    @mcp.tool()
    async def drawing_scene_read(project_id:str,revision_id:str,kind:Literal['schematic','pcb'],sheet:str|None=None,since:str|None=None,instance_path:str|None=None)->dict:
        """读取明确版本的实际候选绘图，不等待CLI导出。since提供旧sceneHash时返回对象增量；缓存缺失则返回全量。coverage列出未覆盖项，绘图不代表ERC/DRC或采纳通过。"""
        return await asyncio.to_thread(reader.read,project_id,revision_id,kind,sheet,since,instance_path)
