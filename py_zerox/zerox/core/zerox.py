import os
import shutil
import tempfile
from typing import List, Optional
from datetime import datetime
import aiofiles
import aiofiles.os as async_os

# Package Imports
from ..processor import (
    convert_pdf_to_images,
    download_file,
    process_page,
    process_pages_in_batches,
)
from ..errors import FileUnavailable
from ..models import litellmmodel
from .types import Page, ZeroxOutput


async def zerox(
    cleanup: bool = True,
    concurrency: int = 10,
    file_path: Optional[str] = "",
    maintain_format: bool = False,
    model: str = "gpt-4o-mini",
    output_dir: Optional[str] = None,
    temp_dir: str = tempfile.gettempdir(),
    custom_system_prompt: Optional[str] = None,
    **kwargs
) -> ZeroxOutput:
    """
    API to perform OCR to markdown using Vision models.
    Please setup the environment variables for the model and model provider before using this API. Refer: https://docs.litellm.ai/docs/providers

    :param cleanup: Whether to cleanup the temporary files after processing, defaults to True
    :type cleanup: bool, optional
    :param concurrency: The number of concurrent processes to run, defaults to 10
    :type concurrency: int, optional
    :param file_path: The path to the PDF file to process
    :type file_path: str, optional
    :param maintain_format: Whether to maintain the format from the previous page, defaults to False
    :type maintain_format: bool, optional
    :param model: The model to use for generating completions, defaults to "gpt-4o-mini". Note - Refer: https://docs.litellm.ai/docs/providers to pass correct model name as according to provider it might be different from actual name.
    :type model: str, optional
    :param output_dir: The directory to save the markdown output, defaults to None
    :type output_dir: str, optional
    :param temp_dir: The directory to store temporary files, defaults to tempfile.gettempdir()
    :type temp_dir: str, optional
    :param custom_system_prompt: The system prompt to use for the model, this overrides the default system prompt of zerox. Generally it is not required unless you want some specific behaviour. When set, it will raise a friendly warning, defaults to None
    :type custom_system_prompt: str, optional

    :param kwargs: Additional keyword arguments to pass to the model.completion -> litellm.completion method. Refer: https://docs.litellm.ai/docs/providers and https://docs.litellm.ai/docs/completion/input
    :return: The markdown content generated by the model.
    """


    input_token_count = 0
    output_token_count = 0
    prior_page = ""
    aggregated_markdown: List[str] = []
    start_time = datetime.now()
    
    # File Path Validators
    if not file_path:
        raise FileUnavailable()

    # Ensure the output directory exists
    if output_dir:
        await async_os.makedirs(output_dir, exist_ok=True)

    # Create a temporary directory to store the PDF and images
    temp_directory = os.path.join(temp_dir or tempfile.gettempdir(), "zerox-temp")
    await async_os.makedirs(temp_directory, exist_ok=True)

    # Download the PDF. Get file name.
    local_path = await download_file(file_path=file_path, temp_dir=temp_directory)
    if not local_path:
        raise FileUnavailable()

    raw_file_name = os.path.splitext(os.path.basename(local_path))[0]
    file_name = "".join(c.lower() if c.isalnum() else "_" for c in raw_file_name)

    # Convert the file to a series of images
    await convert_pdf_to_images(local_path=local_path, temp_dir=temp_directory)

    # Get list of converted images
    images = [
        f"{temp_directory}/{f}"
        for f in await async_os.listdir(temp_directory)
        if f.endswith(".png")
    ]

    # Create an instance of the litellm model interface
    vision_model = litellmmodel(model=model,**kwargs)

    # override the system prompt if a custom prompt is provided
    if custom_system_prompt:
        vision_model.system_prompt = custom_system_prompt

    if maintain_format:
        for image in images:
            result, input_token_count, output_token_count, prior_page = await process_page(
                image,
                vision_model,
                temp_directory,
                input_token_count,
                output_token_count,
                prior_page,
            )
            if result:
                aggregated_markdown.append(result)
    else:
        results = await process_pages_in_batches(
            images,
            concurrency,
            vision_model,
            temp_directory,
            input_token_count,
            output_token_count,
            prior_page,
        )

        aggregated_markdown = [result[0] for result in results if isinstance(result[0], str)]

        ## add token usage
        input_token_count += sum([result[1] for result in results])
        output_token_count += sum([result[2] for result in results])

    # Write the aggregated markdown to a file
    if output_dir:
        result_file_path = os.path.join(output_dir, f"{file_name}.md")
        async with aiofiles.open(result_file_path, "w") as f:
            await f.write("\n\n".join(aggregated_markdown))

    # Cleanup the downloaded PDF file
    if cleanup and os.path.exists(temp_directory):
        shutil.rmtree(temp_directory)

    # Format JSON response
    end_time = datetime.now()
    completion_time = (end_time - start_time).total_seconds() * 1000
    formatted_pages = [
        Page(content=content, page=i + 1, content_length=len(content))
        for i, content in enumerate(aggregated_markdown)
    ]

    return ZeroxOutput(
        completion_time=completion_time,
        file_name=file_name,
        input_tokens=input_token_count,
        output_tokens=output_token_count,
        pages=formatted_pages,
    )
