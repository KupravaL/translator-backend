# app/core/worker.py

import asyncio
import threading
import logging
import time
import queue
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Callable, Optional, Awaitable

# Configure logging
logger = logging.getLogger("worker")

class BackgroundTaskWorker:
    """
    A background task worker that processes translation tasks in a separate thread pool.
    This prevents resource contention with the main API server.
    """
    
    def __init__(self, max_workers: int = 4):
        self.task_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.running = False
        self.worker_thread = None
        self.active_tasks = {}
        self.results = {}
        
    def start(self):
        """Start the worker thread."""
        if self.running:
            return
            
        self.running = True
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        logger.info(f"Background worker started with {self.executor._max_workers} workers")
        
    def stop(self):
        """Stop the worker thread."""
        self.running = False
        if self.worker_thread:
            self.task_queue.put(None)  # Signal to stop
            self.worker_thread.join(timeout=5)
        self.executor.shutdown(wait=False)
        logger.info("Background worker stopped")
        
    def _process_queue(self):
        """Main processing loop that runs in a separate thread."""
        while self.running:
            try:
                task = self.task_queue.get(timeout=1.0)
                if task is None:  # Stop signal
                    break
                    
                task_id, func, args, kwargs = task
                logger.info(f"Processing task {task_id}")
                
                # Update status to in-progress
                self.active_tasks[task_id] = {
                    "status": "in_progress",
                    "started_at": time.time()
                }
                
                # Submit task to thread pool
                future = self.executor.submit(func, *args, **kwargs)
                future.add_done_callback(lambda f, tid=task_id: self._task_complete(tid, f))
                
                self.task_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"Error in worker thread: {e}")
                
    def _task_complete(self, task_id, future):
        """Called when a task completes."""
        try:
            result = future.result()
            status = "completed"
            error = None
        except Exception as e:
            result = None
            status = "failed"
            error = str(e)
            logger.exception(f"Task {task_id} failed: {e}")
            
        # Store result
        self.results[task_id] = {
            "status": status,
            "result": result,
            "error": error,
            "completed_at": time.time()
        }
        
        # Remove from active tasks
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]
            
        logger.info(f"Task {task_id} {status}")
    
    def submit_task(self, task_id: str, func: Callable, *args, **kwargs) -> bool:
        """
        Submit a task to the background worker.
        
        Args:
            task_id: Unique identifier for the task
            func: Function to execute
            *args, **kwargs: Arguments to pass to the function
            
        Returns:
            bool: True if task was submitted, False otherwise
        """
        if not self.running:
            self.start()
            
        # Don't allow duplicate task IDs
        if task_id in self.active_tasks:
            logger.warning(f"Task {task_id} is already running")
            return False
            
        # Submit the task
        self.task_queue.put((task_id, func, args, kwargs))
        self.active_tasks[task_id] = {
            "status": "pending",
            "submitted_at": time.time()
        }
        
        logger.info(f"Submitted task {task_id} to background worker")
        return True
        
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Get the status of a task.
        
        Args:
            task_id: The task ID to check
            
        Returns:
            dict: Task status information
        """
        # Check active tasks first
        if task_id in self.active_tasks:
            return {
                "task_id": task_id,
                "status": self.active_tasks[task_id]["status"],
                "in_progress": True,
                "completed": False
            }
            
        # Check completed tasks
        if task_id in self.results:
            result = self.results[task_id]
            return {
                "task_id": task_id,
                "status": result["status"],
                "in_progress": False,
                "completed": True,
                "error": result.get("error"),
                "completed_at": result.get("completed_at")
            }
            
        # Task not found
        return {
            "task_id": task_id,
            "status": "not_found",
            "in_progress": False,
            "completed": False
        }
        
    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """Get all tasks (active and completed)."""
        tasks = {}
        
        # Add active tasks
        for task_id, info in self.active_tasks.items():
            tasks[task_id] = {
                "task_id": task_id,
                "status": info["status"],
                "in_progress": True,
                "completed": False
            }
            
        # Add completed tasks
        for task_id, result in self.results.items():
            if task_id not in tasks:  # Don't overwrite active tasks
                tasks[task_id] = {
                    "task_id": task_id,
                    "status": result["status"],
                    "in_progress": False,
                    "completed": True,
                    "error": result.get("error"),
                    "completed_at": result.get("completed_at")
                }
                
        return tasks
        
    def clear_old_results(self, max_age_seconds: int = 3600):
        """Clear results older than max_age_seconds."""
        now = time.time()
        to_remove = []
        
        for task_id, result in self.results.items():
            completed_at = result.get("completed_at", 0)
            if now - completed_at > max_age_seconds:
                to_remove.append(task_id)
                
        for task_id in to_remove:
            del self.results[task_id]
            
        logger.info(f"Cleared {len(to_remove)} old task results")

# Create a global worker instance
worker = BackgroundTaskWorker(max_workers=4)