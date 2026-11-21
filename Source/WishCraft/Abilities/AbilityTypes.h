// Shared ability data types for the rule-driven ability sandbox.
#pragma once

#include "CoreMinimal.h"
#include "AbilityTypes.generated.h"

class UAbilityComponentBase;
class UNiagaraSystem;
class USoundBase;

DECLARE_LOG_CATEGORY_EXTERN(LogSandbox, Log, All);

/** The closed set of ability categories. The validation layer caps how many of each may be equipped. */
UENUM(BlueprintType)
enum class EAbilityCategory : uint8
{
	Offense		UMETA(DisplayName = "Offense"),
	Defense		UMETA(DisplayName = "Defense"),
	Mobility	UMETA(DisplayName = "Mobility")
};

/** Allowed numeric range for one named ability parameter. */
USTRUCT(BlueprintType)
struct FAbilityParamRange
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	float Min = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	float Max = 1.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	float Default = 0.f;

	float ClampValue(float InValue) const { return FMath::Clamp(InValue, Min, Max); }
};

/** One row of the token table: what a token means and how it may be configured. */
USTRUCT(BlueprintType)
struct FAbilityTokenDefinition
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	FText DisplayName;

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	EAbilityCategory Category = EAbilityCategory::Offense;

	/** Component spawned (locally on every machine) when this token is equipped. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	TSubclassOf<UAbilityComponentBase> ComponentClass;

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	TMap<FName, float> DefaultParams;

	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	TMap<FName, FAbilityParamRange> ParamRanges;

	/** Plain-text words the stub parser looks for. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite)
	TArray<FString> Keywords;

	/**
	 * One-shot effect played at the moment the ability starts. Played on the server and on every client
	 * that can see the character, so it is visible to spectators and to the other player, not only to the
	 * caster. Leave empty for an ability with no cast moment.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "VFX")
	TObjectPtr<UNiagaraSystem> StartEffect;

	/**
	 * Looping effect attached for as long as the ability is active, then cleared when it ends. Only timed
	 * abilities need this; an instant ability leaves it empty. Intentionally empty for Invisibility, where a
	 * looping aura would outline a player who is supposed to be invisible.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "VFX")
	TObjectPtr<UNiagaraSystem> SustainEffect;

	/** One-shot effect played when the ability ends, e.g. the reappear shimmer. Optional. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "VFX")
	TObjectPtr<UNiagaraSystem> EndEffect;

	/**
	 * Sound played at the moment the ability starts, attached to the same socket as the start effect.
	 * Routed through the same multicast as the VFX, so the other player hears the cast too.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Audio")
	TObjectPtr<USoundBase> StartSound;

	/**
	 * Looping sound held for as long as the ability is active, then stopped when it ends. Only timed
	 * abilities need this. Like SustainEffect, deliberately empty for Invisibility.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Audio")
	TObjectPtr<USoundBase> SustainSound;

	/** One-shot sound played when the ability ends, e.g. the reappear shimmer. Optional. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Audio")
	TObjectPtr<USoundBase> EndSound;

	/** Socket on the avatar mesh to attach to. NAME_None attaches to the mesh origin instead. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "VFX")
	FName AttachSocket = TEXT("hand_r");

	/** Offset from the socket, in the socket's own space. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "VFX")
	FVector EffectOffset = FVector::ZeroVector;

	/** Phrase used in validation errors, e.g. "Invisibility". */
	FString GetDisplayNameString() const { return DisplayName.IsEmpty() ? FString() : DisplayName.ToString(); }
};

/**
 * One tunable ability parameter, as a name/value pair.
 *
 * Deliberately a pair rather than a TMap entry: a replicated struct cannot carry a TMap.
 * FMapProperty::NetSerializeItem logs "Replicated TMaps are not supported" and serialises
 * nothing, so every parameter would arrive empty on every client.
 */
USTRUCT(BlueprintType)
struct FAbilityParam
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly)
	FName ParamName;

	UPROPERTY(BlueprintReadOnly)
	float Value = 0.f;
};

/** An equipped ability: which token, and the (already clamped) parameter values. This is what replicates. */
USTRUCT(BlueprintType)
struct FAbilitySpec
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly)
	FName Token;

	/** Array, not TMap: this struct travels inside the replicated Loadout array. */
	UPROPERTY(BlueprintReadOnly)
	TArray<FAbilityParam> Params;

	/** Reads one parameter, or Fallback when the token has no such parameter. */
	float GetParam(FName InParamName, float Fallback = 0.f) const
	{
		for (const FAbilityParam& Param : Params)
		{
			if (Param.ParamName == InParamName)
			{
				return Param.Value;
			}
		}

		return Fallback;
	}

	/** Adds a parameter, or overwrites its value if it is already present. */
	void SetParam(FName InParamName, float InValue)
	{
		for (FAbilityParam& Param : Params)
		{
			if (Param.ParamName == InParamName)
			{
				Param.Value = InValue;
				return;
			}
		}

		FAbilityParam& Added = Params.AddDefaulted_GetRef();
		Added.ParamName = InParamName;
		Added.Value = InValue;
	}

	/** Map form used inside the ability components, which are not replicated. */
	TMap<FName, float> ToParamMap() const
	{
		TMap<FName, float> Out;
		Out.Reserve(Params.Num());
		for (const FAbilityParam& Param : Params)
		{
			Out.Add(Param.ParamName, Param.Value);
		}

		return Out;
	}
};

/** Replicated runtime state for one equipped token. */
USTRUCT(BlueprintType)
struct FAbilityState
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly)
	FName Token;

	/** Server world time of the last activation, used to derive cooldown locally. */
	UPROPERTY(BlueprintReadOnly)
	float LastActivationServerTime = -10000.f;

	UPROPERTY(BlueprintReadOnly)
	bool bActive = false;
};
