import type {
  ColDef,
  NewValueParams,
  SelectionChangedEvent,
} from "ag-grid-community";
import type { AgGridReact } from "ag-grid-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import ShadTooltip from "@/components/common/shadTooltipComponent";
import CardsWrapComponent from "@/components/core/cardsWrapComponent";
import TableComponent from "@/components/core/parameterRenderComponent/components/tableComponent";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import Loading from "@/components/ui/loading";
import { useGetFilesV2 } from "@/controllers/API/queries/file-management";
import { useDeleteFilesV2 } from "@/controllers/API/queries/file-management/use-delete-files";
import { usePostRenameFileV2 } from "@/controllers/API/queries/file-management/use-put-rename-file";
import { useCustomHandleBulkFilesDownload } from "@/customization/hooks/use-custom-handle-bulk-files-download";
import { customPostUploadFileV2 } from "@/customization/hooks/use-custom-post-upload-file";
import { createFileUpload } from "@/helpers/create-file-upload";
import useUploadFile from "@/hooks/files/use-upload-file";
import BaseModal from "@/modals/baseModal";
import DeleteConfirmationModal from "@/modals/deleteConfirmationModal";
import FilesContextMenuComponent from "@/modals/fileManagerModal/components/filesContextMenuComponent";
import useFileSizeValidator from "@/shared/hooks/use-file-size-validator";
import useAlertStore from "@/stores/alertStore";
import { formatFileSize } from "@/utils/stringManipulation";
import { FILE_ICONS } from "@/utils/styleUtils";
import { cn } from "@/utils/utils";
import { sortByDate } from "../../../utils/sort-agents";
import DragWrapComponent from "./dragWrapComponent";

interface FilesTabProps {
  quickFilterText: string;
  setQuickFilterText: (text: string) => void;
  selectedFiles: any[];
  setSelectedFiles: (files: any[]) => void;
  quantitySelected: number;
  setQuantitySelected: (quantity: number) => void;
  isShiftPressed: boolean;
}

const FilesTab = ({
  quickFilterText,
  setQuickFilterText,
  selectedFiles,
  setSelectedFiles,
  quantitySelected,
  setQuantitySelected,
  isShiftPressed,
}: FilesTabProps) => {
  const { t } = useTranslation();
  type DisplayRow = {
    id: string;
    name: string;
    path: string;
    size: number;
    updated_at?: string;
    created_at?: string;
    progress?: number;
    file?: File;
    rowType: "folder" | "file";
    folderName: string;
    fileCount?: number;
  };

  const tableRef = useRef<AgGridReact<any>>(null);
  const { data: files } = useGetFilesV2();
  const setErrorData = useAlertStore((state) => state.setErrorData);
  const setSuccessData = useAlertStore((state) => state.setSuccessData);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [knowledgeBaseName, setKnowledgeBaseName] = useState("");
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>(
    {},
  );
  const { validateFileSize } = useFileSizeValidator();

  const { mutate: rename } = usePostRenameFileV2();
  const { mutate: deleteFiles, isPending: isDeleting } = useDeleteFilesV2();
  const { handleBulkDownload } = useCustomHandleBulkFilesDownload();

  const handleRename = (params: NewValueParams<any, any>) => {
    if (typeof params.data?.id === "string" && params.data.id.startsWith("folder:")) {
      return;
    }
    rename({
      id: params.data.id,
      name: params.newValue,
    });
  };

  const handleOpenRename = (id: string, name: string) => {
    if (tableRef.current) {
      let targetRowIndex = 0;
      tableRef.current.api.forEachNode((node) => {
        if (node.data?.id === id && node.rowIndex !== null) {
          targetRowIndex = node.rowIndex;
        }
      });
      tableRef.current.api.startEditingCell({ rowIndex: targetRowIndex, colKey: "name" });
    }
  };

  const uploadFile = useUploadFile({ multiple: true });

  const handleUpload = async (files?: File[], kbName?: string) => {
    try {
      const filesIds = await uploadFile({
        files: files,
        knowledgeBaseName: kbName,
      });
      setSuccessData({
        title: t(`File${filesIds.length > 1 ? "s" : ""} uploaded successfully`),
      });
    } catch (error: any) {
      setErrorData({
        title: t("Error uploading file"),
        list: [error.message || t("An error occurred while uploading the file")],
      });
    }
  };

  const { mutate: uploadFileDirect } = customPostUploadFileV2();

  const getKnowledgeBaseNameFromPath = (path: string) => {
    const normalizedPath = path.replace(/\\/g, "/");
    const segments = normalizedPath.split("/").filter(Boolean);
    // Legacy: <user_id>/<kb_name>/<file>
    // New:    <user_id>/<kb_id>/<kb_name>/<file>
    if (segments.length >= 4) return segments[2];
    if (segments.length >= 3) return segments[1];
    return t("Ungrouped");
  };

  const displayRows: DisplayRow[] = useMemo(() => {
    if (!files || !Array.isArray(files)) {
      return [];
    }

    const groups = new Map<string, DisplayRow[]>();
    files.forEach((file) => {
      const folderName = getKnowledgeBaseNameFromPath(file.path);
      const fileRow: DisplayRow = {
        ...file,
        rowType: "file",
        folderName,
      };
      const existing = groups.get(folderName) ?? [];
      existing.push(fileRow);
      groups.set(folderName, existing);
    });

    const folderNames = Array.from(groups.keys()).sort((a, b) =>
      a.localeCompare(b),
    );
    const rows: DisplayRow[] = [];

    folderNames.forEach((folderName) => {
      const groupFiles = groups.get(folderName) ?? [];
      const totalSize = groupFiles.reduce((acc, row) => acc + (row.size ?? 0), 0);
      const latestUpdatedAt = groupFiles
        .map((row) => row.updated_at ?? row.created_at)
        .filter(Boolean)
        .sort()
        .at(-1);

      rows.push({
        id: `folder:${folderName}`,
        name: folderName,
        path: "",
        size: totalSize,
        updated_at: latestUpdatedAt,
        rowType: "folder",
        folderName,
        fileCount: groupFiles.length,
      });

      if (expandedFolders[folderName] !== false) {
        groupFiles
          .sort((a, b) =>
            sortByDate(
              a.updated_at ?? a.created_at ?? "",
              b.updated_at ?? b.created_at ?? "",
            ),
          )
          .forEach((fileRow) => rows.push(fileRow));
      }
    });

    return rows;
  }, [files, expandedFolders]);

  useEffect(() => {
    if (!files || !Array.isArray(files)) return;
    const folderNames = new Set(files.map((file) => getKnowledgeBaseNameFromPath(file.path)));
    setExpandedFolders((prev) => {
      const next = { ...prev };
      folderNames.forEach((folderName) => {
        if (next[folderName] === undefined) {
          next[folderName] = true;
        }
      });
      return next;
    });
  }, [files]);

  useEffect(() => {
    if (files) {
      setQuantitySelected(0);
      setSelectedFiles([]);
    }
  }, [files, setQuantitySelected, setSelectedFiles]);

  useEffect(() => {
    if (!isUploadModalOpen) {
      setPendingUploadFiles([]);
      setKnowledgeBaseName("");
    }
  }, [isUploadModalOpen]);

  const handleSelectionChanged = (event: SelectionChangedEvent<any>) => {
    const selectedRows = event.api
      .getSelectedRows()
      .filter((row: DisplayRow) => row.rowType === "file");
    setSelectedFiles(selectedRows);
    if (selectedRows.length > 0) {
      setQuantitySelected(selectedRows.length);
    } else {
      setTimeout(() => {
        setQuantitySelected(0);
      }, 300);
    }
  };

  const colDefs: ColDef[] = [
    {
      headerName: t("Name"),
      field: "name",
      flex: 2,
      headerCheckboxSelection: true,
      checkboxSelection: (params) => params.data?.rowType === "file",
      editable: true,
      filter: "agTextColumnFilter",
      cellClass:
        "cursor-text select-text group-[.no-select-cells]:cursor-default group-[.no-select-cells]:select-none",
      cellRenderer: (params) => {
        if (params.data.rowType === "folder") {
          const isExpanded = expandedFolders[params.data.folderName] !== false;
          return (
            <button
              className="flex items-center gap-2 font-semibold"
              onClick={(event) => {
                event.stopPropagation();
                setExpandedFolders((prev) => ({
                  ...prev,
                  [params.data.folderName]: !isExpanded,
                }));
              }}
            >
              <ForwardedIconComponent
                name={isExpanded ? "ChevronDown" : "ChevronRight"}
                className="h-4 w-4"
              />
              <ForwardedIconComponent name="Folder" className="h-4 w-4" />
              <span>{params.value}</span>
              <span className="text-xs text-muted-foreground">
                ({params.data.fileCount} {t("files")})
              </span>
            </button>
          );
        }

        const type = params.data.path.split(".").pop()?.toLowerCase() ?? "";
        return (
          <div className="flex min-w-0 items-center gap-4 pl-8 font-medium">
            {params.data.progress !== undefined &&
            params.data.progress !== -1 ? (
              <div className="flex h-6 items-center justify-center text-xs font-semibold text-muted-foreground">
                {Math.round(params.data.progress * 100)}%
              </div>
            ) : (
              <div className="file-icon pointer-events-none relative shrink-0">
                <ForwardedIconComponent
                  name={FILE_ICONS[type]?.icon ?? "File"}
                  className={cn(
                    "-mx-[3px] h-6 w-6 shrink-0",
                    params.data.progress !== undefined
                      ? "text-placeholder-foreground"
                      : (FILE_ICONS[type]?.color ?? undefined),
                  )}
                />
              </div>
            )}
            <div
              className={cn(
                "flex min-w-0 items-center text-sm font-medium",
                params.data.progress !== undefined &&
                  params.data.progress === -1 &&
                  "pointer-events-none text-placeholder-foreground",
              )}
              title={`${params.value}${type ? `.${type}` : ""}`}
            >
              <span className="truncate">{params.value}</span>
              {type && <span className="shrink-0">.{type}</span>}
            </div>
            {params.data.progress !== undefined &&
            params.data.progress === -1 ? (
              <span className="text-xs text-primary">
                {t("Upload failed,")}{" "}
                <span
                  className="cursor-pointer text-accent-pink-foreground underline"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (params.data.file) {
                      uploadFileDirect({ file: params.data.file });
                    }
                  }}
                >
                  {t("try again?")}
                </span>
              </span>
            ) : (
              <></>
            )}
          </div>
        );
      },
    },
    {
      headerName: t("Type"),
      field: "path",
      flex: 1,
      filter: "agTextColumnFilter",
      editable: false,
      valueFormatter: (params) => {
        if (params.data?.rowType === "folder") {
          return "";
        }
        return params.value.split(".").pop()?.toUpperCase();
      },
      cellClass:
        "text-muted-foreground cursor-text select-text group-[.no-select-cells]:cursor-default group-[.no-select-cells]:select-none",
    },
    {
      headerName: t("Size"),
      field: "size",
      flex: 1,
      valueFormatter: (params) => {
        return formatFileSize(params.value);
      },
      editable: false,
      cellClass:
        "text-muted-foreground cursor-text select-text group-[.no-select-cells]:cursor-default group-[.no-select-cells]:select-none",
    },
    {
      headerName: t("Modified"),
      field: "updated_at",
      valueFormatter: (params) => {
        if (params.data?.rowType === "folder") {
          return "";
        }
        return params.data.progress
          ? ""
          : new Date(params.value + "Z").toLocaleString();
      },
      editable: false,
      flex: 1,
      resizable: false,
      cellClass:
        "text-muted-foreground cursor-text select-text group-[.no-select-cells]:cursor-default group-[.no-select-cells]:select-none",
    },
    {
      maxWidth: 60,
      editable: false,
      resizable: false,
      cellClass: "cursor-default",
      cellRenderer: (params) => {
        if (params.data?.rowType === "folder") {
          return <></>;
        }
        return (
          <div className="flex h-full cursor-default items-center justify-center">
            {!params.data.progress && (
              <FilesContextMenuComponent
                file={params.data}
                handleRename={handleOpenRename}
              >
                <Button variant="ghost" size="iconMd">
                  <ForwardedIconComponent name="EllipsisVertical" />
                </Button>
              </FilesContextMenuComponent>
            )}
          </div>
        );
      },
    },
  ];

  const onFileDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const droppedFiles = Array.from(e.dataTransfer.files);
    if (droppedFiles.length > 0) {
      await handleUpload(droppedFiles);
    }
  };

  const handleDownload = () => {
    handleBulkDownload(
      selectedFiles,
      setSuccessData,
      setErrorData,
      setIsDownloading,
    );
  };

  const handleDelete = () => {
    deleteFiles(
      {
        ids: selectedFiles.map((file) => file.id),
      },
      {
        onSuccess: (data) => {
          setSuccessData({ title: data.message });
          setQuantitySelected(0);
          setSelectedFiles([]);
        },
        onError: (error) => {
          setErrorData({
            title: t("Error deleting files"),
            list: [
              error.message || t("An error occurred while deleting the files"),
            ],
          });
        },
      },
    );
  };

  const UploadButtonComponent = useMemo(
    () => (
      <ShadTooltip content={t("Upload File")} side="bottom">
        <Button
          className="!px-3 md:!px-4 md:!pl-3.5"
          onClick={() => {
            setIsUploadModalOpen(true);
          }}
          id="upload-file-btn"
          data-testid="upload-file-btn"
        >
          <ForwardedIconComponent
            name="Plus"
            aria-hidden="true"
            className="h-4 w-4"
          />
          <span className="hidden whitespace-nowrap font-semibold md:inline">
            {t("Upload Files")}
          </span>
        </Button>
      </ShadTooltip>
    ),
    [t],
  );

  return (
    <div className="flex h-full flex-col">
      <BaseModal
        size="small"
        open={isUploadModalOpen}
        setOpen={setIsUploadModalOpen}
      >
        <BaseModal.Header description={t("Enter a knowledge base name before selecting files.")}>
          {t("Upload Files")}
        </BaseModal.Header>
        <BaseModal.Content>
          <div className="flex flex-col gap-3">
            <Input
              placeholder={t("Knowledge base name")}
              value={knowledgeBaseName}
              onChange={(event) => {
                setKnowledgeBaseName(event.target.value);
              }}
              data-testid="knowledge-base-name-upload-input"
            />
            <div className="text-sm text-muted-foreground">
              {pendingUploadFiles.length > 0
                ? t("{{count}} file(s) selected", { count: pendingUploadFiles.length })
                : t("No files selected yet")}
            </div>
            {pendingUploadFiles.length > 0 && (
              <div className="max-h-48 overflow-auto rounded-md border p-2">
                <div className="flex flex-col gap-1.5">
                  {pendingUploadFiles.map((file) => {
                    const fileType = file.name.split(".").pop()?.toLowerCase() ?? "";
                    const fileIcon = FILE_ICONS[fileType]?.icon ?? "File";
                    const fileIconColor = FILE_ICONS[fileType]?.color ?? "text-muted-foreground";

                    return (
                      <div
                        key={`${file.name}-${file.size}`}
                        className="flex items-center justify-between rounded-md border bg-muted/30 px-2 py-1.5"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <ForwardedIconComponent
                            name={fileIcon}
                            className={cn("h-4 w-4 shrink-0", fileIconColor)}
                          />
                          <span className="truncate text-sm">{file.name}</span>
                        </div>
                        <div className="ml-2 flex shrink-0 items-center gap-2">
                          <span className="rounded bg-background px-1.5 py-0.5 text-xxs uppercase text-muted-foreground ring-1 ring-border">
                            {fileType || t("file")}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {formatFileSize(file.size)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </BaseModal.Content>
        <BaseModal.Footer
          submit={{
            label: t("Upload Knowledge Base"),
            dataTestId: "upload-files-with-kb-button",
            disabled:
              pendingUploadFiles.length === 0 || !knowledgeBaseName.trim(),
            onClick: async () => {
              const kbName = knowledgeBaseName.trim();
              if (!kbName) {
                setErrorData({
                  title: t("Knowledge base name is required"),
                });
                return;
              }
              await handleUpload(pendingUploadFiles, kbName);
              setIsUploadModalOpen(false);
              setKnowledgeBaseName("");
              setPendingUploadFiles([]);
            },
          }}
        >
          <Button
            variant="outline"
            type="button"
            onClick={async () => {
              try {
                const selected = await createFileUpload({
                  multiple: true,
                  accept: "",
                });
                const validFiles: File[] = [];
                for (const file of selected) {
                  validateFileSize(file);
                  validFiles.push(file);
                }
                setPendingUploadFiles(validFiles);
              } catch (error: any) {
                setErrorData({
                  title: t("Error selecting files"),
                  list: [error.message || t("Could not select files")],
                });
              }
            }}
          >
            {t("Choose Files")}
          </Button>
        </BaseModal.Footer>
      </BaseModal>

      {files && files.length !== 0 ? (
        <div className="flex justify-between">
          <div className="flex w-full xl:w-5/12">
            <Input
              icon="Search"
              data-testid="search-store-input"
              type="text"
              placeholder={t("Search files...")}
              className="mr-2 w-full"
              value={quickFilterText || ""}
              onChange={(event) => {
                setQuickFilterText(event.target.value);
              }}
            />
          </div>
          <div className="flex items-center gap-2">{UploadButtonComponent}</div>
        </div>
      ) : (
        <></>
      )}

      <div className="flex h-full flex-col py-4">
        {!files || !Array.isArray(files) ? (
          <div className="flex h-full w-full items-center justify-center">
            <Loading />
          </div>
        ) : files.length > 0 ? (
          <DragWrapComponent onFileDrop={onFileDrop}>
            <div className="relative h-full">
              <TableComponent
                rowHeight={45}
                headerHeight={45}
                cellSelection={false}
                tableOptions={{
                  hide_options: true,
                }}
                suppressRowClickSelection={!isShiftPressed}
                editable={[
                  {
                    field: "name",
                    onUpdate: handleRename,
                    editableCell: true,
                  },
                ]}
                rowSelection="multiple"
                onSelectionChanged={handleSelectionChanged}
                columnDefs={colDefs}
                rowData={displayRows}
                className={cn(
                  "ag-no-border group w-full",
                  isShiftPressed && quantitySelected > 0 && "no-select-cells",
                )}
                pagination
                ref={tableRef}
                quickFilterText={quickFilterText}
                gridOptions={{
                  stopEditingWhenCellsLoseFocus: true,
                  ensureDomOrder: true,
                  colResizeDefault: "shift",
                }}
              />

              <div
                className={cn(
                  "pointer-events-none absolute top-1.5 z-50 flex h-8 w-full transition-opacity",
                  selectedFiles.length > 0 ? "opacity-100" : "opacity-0",
                )}
              >
                <div
                  className={cn(
                    "ml-12 flex h-full flex-1 items-center justify-between bg-background",
                    selectedFiles.length > 0
                      ? "pointer-events-auto"
                      : "pointer-events-none",
                  )}
                >
                  <span className="text-xs text-muted-foreground">
                    {t("{{count}} selected", { count: quantitySelected })}
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="iconMd"
                      onClick={handleDownload}
                      loading={isDownloading}
                      data-testid="bulk-download-btn"
                    >
                      <ForwardedIconComponent name="Download" />
                    </Button>

                    <DeleteConfirmationModal
                      onConfirm={handleDelete}
                      description={"file" + (quantitySelected > 1 ? "s" : "")}
                    >
                      <Button
                        variant="destructive"
                        size="iconMd"
                        className="px-2.5 !text-mmd"
                        loading={isDeleting}
                        data-testid="bulk-delete-btn"
                      >
                        <ForwardedIconComponent name="Trash2" />
                        {t("Delete")}
                      </Button>
                    </DeleteConfirmationModal>
                  </div>
                </div>
              </div>
            </div>
          </DragWrapComponent>
        ) : (
          <CardsWrapComponent
            onFileDrop={onFileDrop}
            dragMessage={t("Drop files to upload")}
          >
            <div className="flex h-full w-full flex-col items-center justify-center gap-8 pb-8">
              <div className="flex flex-col items-center gap-2">
                <h3 className="text-2xl font-semibold">{t("No files")}</h3>
                <p className="text-lg text-secondary-foreground">
                  {t("Upload files or import from your preferred cloud.")}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {UploadButtonComponent}
              </div>
            </div>
          </CardsWrapComponent>
        )}
      </div>
    </div>
  );
};

export default FilesTab;
